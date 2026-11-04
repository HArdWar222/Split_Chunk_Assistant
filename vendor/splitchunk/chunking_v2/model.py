"""fac_rag.chunking_v2.model —— v2 切块层的对象模型（技术版 §3）。

M1 只落「对象形状」：后续 ``regions.py`` / ``units.py`` / ``tables.py`` /
``render.py`` / ``emitter.py`` / ``gate.py`` 都围绕这些 dataclass 工作。M1 的
入口 pipeline 还是 stub（见 ``fac_rag/chunking_v2/__init__.py``），对象先建好，
M2 起逐步填实。

设计约束（D-036 / D-037 / D-040）
--------------------------------
* 一个 ``.md`` = 一份 :class:`Document`，**不做子文档拆分**（D-036）；
* 章名/节名只进 :attr:`Unit.path` 面包屑，不进契约字段（D-037 + Q2）；
* 表格用 :class:`TableModel` 独立建模，不在 ``Unit.lines`` 里塞管道符；
* 契约 15 字段在 emitter（M2）统一映射，本模块不写 ``id`` / ``chunk_hash``；
  extras（region / namespace / appendix / probe 等）走侧车（D-040），
  所以 :class:`ChunkDraft` 里只有正文与渲染需要的字段。

M1 的 :class:`ParseResult` 是**过渡兼容壳**：字段名与 v1
``fac_rag.chunking.base.ParseResult`` 一致，保证 ``chunk_file`` 返回结构在
M1 就与 v1 同形；M2 起 Document/Unit 成为唯一入口，它只保留到 emitter 落地。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

#: 区域种类（技术版 §3 / §5）
RegionKind = Literal["front", "toc", "body", "appendix", "commentary", "references"]

#: 引用单位种类（技术版 §3 / §6）
UnitKind = Literal[
    "article",   # 条文式：一条
    "clause",    # 标准式：一个条款号
    "chapter",   # 章（只进面包屑，一般不单独成块）
    "section",   # 节（只进面包屑）
    "appendix",  # 附录整体
    "table",     # 表格行/表题
    "paragraph",  # 无编号段落（generic 族）
]

#: 文档族（技术版 §3 / §4）
Family = Literal["statute", "standard", "generic"]


class ChunkingError(RuntimeError):
    """v2 切块失败（profile 缺失、派工单两处配置不一致、一块都没切出来之类）。

    与 v1 ``fac_rag.chunking.base.ChunkingError`` 语义一致；两个包共存期间
    各自持有一个同类异常，调用方按 ``RuntimeError`` 捕获即可。
    """


# ---------------------------------------------------------------------------
# profile（configs/profiles/*.yaml 的对象形状，技术版 §3）
# ---------------------------------------------------------------------------


@dataclass
class MatchRules:
    """profile 匹配规则：``require`` 必须全部命中，``weight`` 加权打分，``forbid`` 扣分。"""

    require: list[str] = field(default_factory=list)
    weight: dict[str, float] = field(default_factory=dict)
    forbid: list[str] = field(default_factory=list)


@dataclass
class TitleRule:
    """文档标题从哪来、怎么洗。"""

    source: str = "first_heading"        # first_heading | front_matter | filename
    strip_chars: str = "　 \t#"


@dataclass
class FrontRule:
    """文件头区域：丢弃 + 抽取元数据（D-037：只存 Document.meta，不落契约）。"""

    drop: bool = True
    metadata: list[str] = field(default_factory=list)   # 要抽的键，如 standard_id/publish_date


@dataclass
class TocRule:
    """目次区域：丢弃 + 条目识别用的页码形态。"""

    drop: bool = True
    page_forms: list[str] = field(
        default_factory=lambda: ["single_space", "fullwidth", "dot_leader", "paren"]
    )
    max_scan_lines: int = 600


@dataclass
class RegionPolicy:
    """各区域去向：``drop`` 丢弃记账，``keep`` 进正文（技术版 §5）。"""

    front: str = "drop"
    toc: str = "drop"
    appendix: str = "keep"
    commentary: str = "drop"
    references: str = "drop"


@dataclass
class UnitRule:
    """单位识别规则（技术版 §6）。正则保留字符串，M2 的 units.py 负责编译。"""

    article_pattern: str | None = None
    clause_pattern: str | None = None
    top_pattern: str | None = None
    heading_mode: str = "auto"            # auto | on | off（on=条号写在 markdown 标题里）
    default_kind: str = "clause"
    paragraph_split: bool = False         # generic：按标题/空行切段


@dataclass
class AppendixRule:
    """附录识别（技术版 §5 / §6）：显式区域 + 附录内独立编号。

    ``mode``（D-042）：

    * ``relabel``（默认，M2 实装）——附录内被挂到正文条号上的兜底块只做
      **块级重标注**：``law_num`` 改成「附 录 X」、``chunk_kind=appendix``、
      ``extras.appendix`` 记字母/类型/标题；块文本、顺序、块数保持 v1 不变，
      这样 60 份 L1 文件 hash 全同（D3 的引用归属在契约层修对，面包屑重写
      推迟到 M5 用新 fixture 评估）。
    * ``unit``（M5 规划，M2 未实现）——附录标题立刻产出独立 ``appendix``
      单元，附录内单元改用附录面包屑。
    """

    enabled: bool = True
    heading_pattern: str | None = None
    unit_pattern: str | None = None
    kinds: list[str] = field(default_factory=lambda: ["规范性", "资料性"])
    mode: str = "relabel"


@dataclass
class TableRule:
    """表格规则（技术版 §7）。

    ``mode``（M3 / D-043）：

    * ``model``（默认）——v2 的 ``TableModel`` 渲染：D4 续表合并（无表题跨页续表
      并回主表、``单位为日`` 单位行吞掉）+ D5 单元格清洗（``**``/HTML/LaTeX
      转义/续页标记），块文本不再残留 ``|`` 等脏字符；
    * ``legacy``——原样复用 v1 :mod:`fac_rag.chunking.tables` 的实现，用于
      「等价模式」回归：关掉修复后应与 M2 的 61/65 hash 等价完全一致。

    ``unlabeled``（M8b / task-12）——识别**无表题表格**：HTML / 管道通道只要
    结构信号合格（能解析出网格、表头 + ≥2 行数据、列数一致）就按出现顺序编号
    ``表1``、``表2``…，表题留空、不编造。默认关闭（``False``）：现役 65 份
    生产语料零回归（GB 30871 的 16 块无表题作业票表单等保持原样），只在
    ``generic`` 等兜底 profile 里显式打开。
    """

    sources: list[str] = field(default_factory=lambda: ["pipe", "html"])
    caption_pattern: str | None = None
    continuation_markers: list[str] = field(default_factory=lambda: ["（续）", "(续)"])
    header_rows: int = 0
    row_pack_chars: int = 700
    mode: str = "model"
    unlabeled: bool = False
    #: D-071：无表题表格从最近的标题/加粗行派生表名（当前只在附录区启用，
    #: gb_standard 打开；tabular 用 unlabeled 的「表N」编号，不派生）。
    derive_title: bool = False
    #: Phase 1.1：单行渲染文本超过 ``RenderRule.max_chars`` 时的降级方式。
    #: ``cell``（默认）按单元格边界拆段、单段仍超再按标点硬切；``hard`` 保持
    #: 旧行为（超长行独占一块，可产出 over_max 块）。
    row_overflow: str = "cell"
    #: Phase 1.2：跨页续表宽容匹配阈值。``1.0``（默认）保持旧行为（表头逐格
    #: 完全相等）；``<1.0`` 时按「合并列名归一化」后的列匹配率判定，容忍跨页
    #: 表头的空格差异与「（续）」标记。
    cont_tolerance: float = 1.0


@dataclass
class RenderRule:
    """渲染/清洗规则（技术版 §8）。"""

    breadcrumb_depth: int = 2
    breadcrumb_sep: str = " > "
    clean: list[str] = field(
        default_factory=lambda: [
            "latex_escapes",
            "md_bold",
            "html_tags",
            "page_tail",
            "continuation_markers",
        ]
    )
    max_chars: int = 800


@dataclass
class SplitRule:
    """超长拆 part（part_index / part_total 落契约）。

    Phase 2 新增四个**可选**切分策略；默认值全部等价于旧行为（split_text
    只按行打包 + 单行超长硬切），因此不改任何现有 profile 的输出。
    """

    max_chars: int = 800
    part_suffix: str = "-p{i}"
    #: 软上限（Unstructured ``new_after_n_chars`` 语义）：长度达到该值后遇到
    #: 切分点就收块；``0`` = 关闭（旧行为，只在 ``max_chars`` 处收块）。
    soft_max_chars: int = 0
    #: 小段回吸（``combine_text_under_n_chars`` 语义）：短于该值的相邻块尝试
    #: 合并；``0`` = 关闭（旧行为）。
    combine_under_chars: int = 0
    #: 切分点优先级（高 → 低），如 ``["\\n\\n", "\\n", "。", "；", "，"]``。
    #: 空列表 = 旧行为（只用「。」「；」「，」在 max_chars 窗口内找切点）。
    separators: list[str] = field(default_factory=list)
    #: 代码围栏保护：``True`` 时不把 ``` / ~~~ 围栏块切开（超长围栏按内容
    #: 重打成多块、每块自带围栏标记）；``False`` = 旧行为。
    protect_fences: bool = False


@dataclass
class GateRule:
    """第⑤步质检阈值（技术版 §10）。"""

    min_line_coverage: float = 0.60
    max_bad_block_ratio: float = 0.02
    max_dup_ref_ratio: float = 0.01
    block_count_tolerance: float = 0.40
    max_warnings: int = 5
    #: Phase 3：表格块内部不变量（grid 宽度/表头/行宽/同表一致性）是否升级为
    #: 失败项。``False``（默认）只记 ``metrics.table_invariants``，不影响 ok。
    strict_tables: bool = False


@dataclass
class Profile:
    """一份格式方言的完整切块策略（= configs/profiles/<name>.yaml）。"""

    name: str = ""
    family: Family = "generic"
    description: str = ""
    match: MatchRules = field(default_factory=MatchRules)
    title: TitleRule = field(default_factory=TitleRule)
    front: FrontRule = field(default_factory=FrontRule)
    toc: TocRule = field(default_factory=TocRule)
    regions: RegionPolicy = field(default_factory=RegionPolicy)
    units: UnitRule = field(default_factory=UnitRule)
    appendix: AppendixRule = field(default_factory=AppendixRule)
    tables: TableRule = field(default_factory=TableRule)
    render: RenderRule = field(default_factory=RenderRule)
    split: SplitRule = field(default_factory=SplitRule)
    gate: GateRule = field(default_factory=GateRule)
    #: 加载期校验的告警（M6/K5）：未接线的字段、非法 match 特征名等；
    #: ``load_profiles`` 同时用 ``warnings.warn`` 打出来，不静默忽略。
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 文档 / 区域 / 单位 / 表格（技术版 §3 / §5 / §6 / §7）
# ---------------------------------------------------------------------------


@dataclass
class Region:
    """文档里的一个连续区域，用行区间表示：``[start, end)``（0 基，end 不含）。"""

    kind: RegionKind
    start: int
    end: int
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def n_lines(self) -> int:
        return max(0, self.end - self.start)


@dataclass
class TableModel:
    """一张表 + 它的全部续表（技术版 §7）。

    ``grids`` 的第一维是「主表 + 各段续表」，第二维是行，第三维是单元格；
    这样续表可以独立保留表头差异，渲染时再决定怎么拼。
    """

    label: str = ""                                    # 表号，如「表2」「表A.1」
    title: str = ""                                    # 表题
    grids: list[list[list[str]]] = field(default_factory=list)
    header_rows: int = 1
    meta_rows: int = 0
    col_names: list[str] = field(default_factory=list)
    row_pack_chars: int = 700


@dataclass
class Unit:
    """一个待装盒的引用单位（技术版 §6）。

    ``path`` 是显式祖先链（章 > 节 > …），只用于面包屑（D-037 / Q2）；
    ``ref`` 是要写进契约 ``law_num`` 的引用键（第一条 / 4.1.2 / A.1 / 表A.1）。
    """

    kind: UnitKind
    ref: str
    title: str = ""
    region: RegionKind = "body"
    path: tuple[str, ...] = ()
    lines: list[str] = field(default_factory=list)
    table: TableModel | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Document:
    """一份文档 = 一个 .md 文件（D-036：不拆子文档）。"""

    path: Path
    law_name: str
    group: str
    lines: list[str]
    profile: Profile
    meta: dict[str, Any] = field(default_factory=dict)
    regions: list[Region] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class ChunkDraft:
    """渲染完、待 emitter 映射成契约块的中间块。

    只带「块正文 + 引用键 + 块种类 + part 信息」；契约 15 字段由 emitter 统一
    补（D-040：extras 不进块本体，走 ``data/chunks/_extras.jsonl`` 侧车）。
    """

    text: str
    law_num: str = ""
    chunk_kind: str = "clause"
    part_index: int = 1
    part_total: int = 1
    extras: dict[str, Any] = field(default_factory=dict)


#: M1 过渡兼容壳：字段名与 v1 ``fac_rag.chunking.base.ParseResult`` 一致。
@dataclass
class ParseResult:
    """M1 stub 的解析结果（不是最终模型，M2 起由 Document/Unit 取代）。"""

    title: str = ""
    units: list[Unit] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_lines: int = 0
    #: 解析过程的附加信息（auto 档的检测决策日志等）；不进块契约、不落 jsonl
    meta: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "AppendixRule",
    "ChunkDraft",
    "ChunkingError",
    "Document",
    "Family",
    "FrontRule",
    "GateRule",
    "MatchRules",
    "ParseResult",
    "Profile",
    "Region",
    "RegionKind",
    "RegionPolicy",
    "RenderRule",
    "SplitRule",
    "TableModel",
    "TableRule",
    "TitleRule",
    "TocRule",
    "Unit",
    "UnitKind",
    "UnitRule",
]
