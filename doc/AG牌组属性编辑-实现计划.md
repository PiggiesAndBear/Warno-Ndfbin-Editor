# AG 牌组属性编辑 —— 实现计划（第二个专用页）

> **依据**：`doc/移交-WarnoNdfbinEditor-AG牌组属性编辑.md`（移交方 NdfbinDecksEditor）
> ＋ 本轮对柏林 mod `Pawn.ndfbin` 的**独立实测复核**
> **日期**：2026-10-17 ｜ **状态**：**P0 / P1 已实施并通过验收**（数值属性 + 批量改 + 撤销接线）
> **目标读者**：本项目后续实现者（含未来的自己）

---

## 0. 一句话结论

**可以做，而且比移交文档预估的简单。** 前置门槛（逐字节往返）已通过；
缺的不是数据能力，是三层新代码：**只读元数据通道**、**pawn 对模块的投影**、
**clone-on-write 写入器**。而「多条件筛选 + 批量改」这一块——
也就是本项目用户最关心的那部分——**内核已经写好了**，直接复用。

---

## 1. 前置门槛：已通过 ✅

移交文档 §5 提了一个硬前提：「先用你们的读写链对 `Pawn.ndfbin` 做一次
decode → encode → 逐字节比对。通不过就先别做属性编辑。」

实测（`WarnoAGF_berlin_1989/Gen/NDF/GFX/Pawn.ndfbin`）：

| 项 | 实测 |
|---|---|
| 文件 | 212,722 B ｜ md5 `3898e934e97659a503cab69f343c3983` |
| 解码 | **0.86 s** ｜ 18,285 对象 ｜ 39 类 |
| 编码 | 0.10 s ｜ 212,722 → 212,722 B |
| **往返** | **md5 逐字节相同** ✅ |

移交文档说该文件「嵌套深度和对象数都比 `Deck.ndfbin` 大，不能假设另一个文件
能过这个也能过」—— 现在这个文件本身也过了。

---

## 2. 移交文档的勘误与补充（都是实测）

这份移交文档的**结构性判断全部正确**（属性不在 Deck 里、模块被去重合并、
必须 clone-on-write、连接率 100%）。以下五处需要修正或补充：

| # | 移交文档原文 | 实测 | 影响 |
|---|---|---|---|
| 1 | 「用 `TDeckModuleDescriptor.DeckIdentifier` 作连接键」 | 字段**确实存在**（2207 有值），但**取不到** —— 它被 `schema._editable_text()` 判为标识符而排除在模型外 | **必须新增只读通道**，否则专用页连「这个 pawn 叫什么」都不知道 |
| 2 | 隐含「按固定段位切国家/师/营」 | `DeckIdentifier` 段数是 **2/3/4/5/6 段不等**（58/244/811/1141/11 个） | 不能按下标硬切；规则见 §5.1 |
| 3 | 图标 17 种 | 正确，但载体是**每条 2 个元素的 list**：`[Texture_STRATEGIC_RTS_H_x, Texture_STRATEGIC_x]` | 改图标要**两个一起改**；`RTS_H_` 是同类的第二变体，不是另一类（解答了 §8.4 的疑问） |
| 4 | 附录 A 未列 | 多一个参与类：`TBUCKToolAlternativeValues_TUIValueTextureNameFromTEugBMutableInteger`（2265 个，每营一个，装图标） | 它是**独有**对象，可原地改 |
| 5 | 「保存要写两个文件」 | 属性**全部**在 `Pawn.ndfbin`；`Deck.ndfbin` 只装编制、不含任何战略属性 | **单文件即可**，见 §4.1 |

另外三条移交文档标了「已知但未定」，本轮把能定的定掉：

- **`BattleSupportRadiusInAPCase` 只覆盖 582 / 2268 个营**（移交文档 §4 写 582，
  实测一致）。字段缺失是常态，不是错误。
- **`MaxFatigue` 2265 个营全是 8**，只有 1 种取值 —— 改它等于「引入差异」而非
  「修正不同」，与移交文档对 `Score` 的告诫同理。
- **`BattleRole` 1169 / 2268 有值，3 种取值**；整数 ↔ 枚举名的对应**仍未验证**，
  不要凭「471 个 howitzer ≈ 471 个 = 3」写死映射。

---

## 3. 现有能力盘点：一半的活已经干完了

用户要的两件事——**按维度筛对象**与**按属性多条件筛 + 批量改**——本项目已有一半。

| 模块 | 已有什么 | 对 AG 专用页的用处 | 结论 |
|---|---|---|---|
| `ndfedit/query.py` | `Condition`（等值/≥/≤/区间/多选）、`Query`（AND）、`is_enum_like` 自动识别枚举、float 按**显示精度**比较、`preview()` / `apply_value()`、`BulkResult`（命中/改动/未变 + 改前分布） | **就是用户要的「属性多条件筛选 + 批量改」** | ★ **直接复用**，无需新写 |
| `ndfedit/schema.py` | `ObjEntry` 模型、字段六分类、覆盖率统计、`clone_object()`（追加 + 自动换 GUID + 撤销栈友好）、`changes()` 改动预览 | clone-on-write 的「克隆」这一步**已有** | ★ 复用 |
| `ndfedit/refs.py` | `RefIndex`（引用索引、反查 class_id）、`RefListField`（增删/重排/换目标）、`ref_count` 引用计数 | 改 `ModulesDescriptors` 里那一条指向、算**共享度** | ★ 复用 |
| `ndfedit/history.py` | 脏状态差分撤销栈 | 批量改天然一步 | ★ 复用 |
| `ndfedit/ammo_page.py` | `ConditionRow` + 结果表 + 批量改 + 派生列的**专用页范式** | 新页照着搭 | ★ 复用范式 |
| `core/writer.py` | 原子写入 + `.bak` | 落盘 | ★ 复用 |
| `io/modlocator.py` | mod / 文件定位 | 打开 Pawn.ndfbin | ★ 复用 |

**缺口只有三个**（§4）——这决定了工作量比预想的小。

---

## 4. 三个设计决策

### 4.1 单文件，不是双文件 ★

移交文档 §6 写「保存要写两个文件」，那是**从牌组编辑器的视角**看的
（在 deck 里选营 → 改 pawn）。但专用页的正确形态是：

```
打开 Pawn.ndfbin  →  列出 2265 个营  →  按维度筛  →  批量改属性
```

**属性全在这一个文件里**，`Deck.ndfbin` 只是编制（`DeckPackList` 之类），
不含任何战略属性。所以：

- 专用页**只要求 `Pawn.ndfbin`**，与现有「一个窗口一个文件」的架构天然一致；
- 不需要双文件的保存协调、不需要两侧各自的 `.bak`；
- 用户在 `Deck.ndfbin` 里点这个入口时，**照 `ammo_page` 的做法给一句提示**
  （「战略属性在 Pawn.ndfbin 里，换一个文件再试」）。

> 如果以后要做「跨文件看编制」，那是另一个功能，不混进这一期。

### 4.2 核心新概念：pawn 视图（把模块属性投影成字段）

AG 的属性和普通对象**长得不一样**：它们不在 pawn 自己身上，而在它
`ModulesDescriptors` 指向的**模块对象**上。

```
TEntityDescriptor #34  (Pawn_pion_BEL_11Sq)
   └─ ModulesDescriptors = [ … , #9012, #9033, #9050, … ]
                                │      │      │
                                │      │      └─ TStrategicLabelModuleDescriptor
                                │      │            .BackgroundTexture → #59
                                │      └─ TActionPointsModuleDescriptor
                                │            .InitialActionPoint = 12.0
                                └─ TTypeUnitModuleDescriptor
                                     .MotherCountry = 'BEL'
```

所以 `query.py` 那种「读 `entry.fields[name]`」的筛选**直接用不了** ——
pawn 身上根本没有 `InitialActionPoint` 这个字段。

**方案**：引入一个**视图层**，把要编辑的属性从模块投影到 pawn 上，产出一个
形状与 `ObjEntry` 兼容的对象：
`PawnView.fields = {'InitialActionPoint': Field(...), 'MotherCountry': ..., …}`
筛选、结果表、批量改**全部复用现有 `query.py`**，一行都不用改。

这一层的自然后果：**"虚拟字段"是这条路的关键抽象** —— 它同时装得下
「来自模块的属性」和「从名字解析出来的维度」（国家/师/营）。

### 4.3 clone-on-write：复用 `clone_object()`，不新增内核

| 属性类别 | 例子 | 改法 |
|---|---|---|
| **独有模块**（共享度 ~1.0） | 部队类型图标、`Score`、`MotherCountry`、`NameToken` | **原地改**，零爆炸半径 |
| **共享模块**（共享度 ≫ 1） | 行动点（47）、移动方式（378）、战斗角色（206） | **克隆 → 只把本营那一条 ref 指向克隆体 → 改克隆体** |

「克隆」这一步 `NdfDocument.clone_object()` 已经能做（追加到文件末尾、
`set_appended_count()` 物化、撤销栈当作一步、自动换 `type_id==0x1A` 的 GUID）。

需要新写的是一个薄层：

```
set_attr(pawn_view, module_cls, field, value) -> bool   # 返回是否克隆了
    mod = pawn.modules[module_cls]
    if ref_count[mod] > 1:            # ★ 也是界面上那个 ★★ 标记的来源
        mod = nd.clone_object(entry_of(mod)).object_id
        pawn.set_module_ref(module_cls, mod)     # 改 ModulesDescriptors 里那一条
    write_field(mod, field, value)
```

⚠️ **一个必须实测的点**：`clone_object()` 会自动换 GUID，而**模块对象上有没有
GUID 字段**（`DescriptorId`，`type_id 0x1A`）还没查。有就自动换了（正合适），
没有就什么都不做（也无害）——但**要确认**，因为「克隆体与原件共用身份」在
某些模块上可能是错的。

---

## 5. 筛选设计（用户的核心诉求）

用户明确要两类筛选：**按名称维度**（国家前缀 / 师 / 营）和
**按属性多条件**。这两类在实现上是同一个机制（都是 `Condition` 打在虚拟字段上），
但**取值域来源完全不同**，界面上要分开呈现。

### 5.1 名称维度：`DeckIdentifier` 怎么切

实测 `DeckIdentifier` 的形态（2265 个营）：

| 段数 | 个数 | 形态 | 例子 |
|---|---|---|---|
| 2 | 58 | `AGF_<hash>` —— **机场/补给站，不是营** | `AGF_005cf9faae066908` |
| 3 | 244 | `pion_<国家>_<营>` 直属 | `pion_BEL_11Sq` |
| 4 | 811 | `pion_<国家>_<师>_<营>` | `pion_BEL_1DI_12Linie` |
| 5 | **1141** | `pion_<国家>_<师>_<团>_<营>` | `pion_CAN_1DIV_R22R_1` |
| 6 | 11 | `…_<军>_<师>_<团>_<营>` | `pion_SOV_8GA_34ArtDiv_4_1` |

**段数不固定 = 编制层级深度不同**，不是随机。规律稳定：

```
parts    = id.split('_')
kind     = parts[0]        # 'pion'(部队) / 'AGF'(设施)
country  = parts[1]        # 国家
unit     = parts[-1]       # 营
parents  = parts[2:-1]     # 上级链：师 / 团 / …（可能为空）
```

→ **不能按固定下标切**，必须按「首段 / 末段 / 中间」来切。

**国家还有一条更可靠的路**：`TTypeUnitModuleDescriptor.MotherCountry`
是**结构化字段**（2268/2268 全覆盖，14 种：SOV 495 / RFA 404 / US 246 /
POL 244 / RDA 229 / TCH 219 / FR 122 / UK 102 / NL 77 / BEL 43 / DDR 37 /
CAN 28 / ESP 20 / HON 2）。

两者**不等价**，实测有两处不一致：

- `MotherCountry=DDR` 的 28 个营，其 `DeckIdentifier` 第 1 段不是 `DDR`
  （东德阵营在名字里用了别的写法）；
- `MotherCountry=CAN` 的 11 个营，第 1 段是 `FR`（加拿大部队编在法国师下）。

> **⚠ 这条决策在实施时被推翻了。** 原判据是「`MotherCountry` 结构化、语义准」，
> 但实测它**在柏林 mod 里有 139/2268 处是错的，而且成片出现**：
> `pion_BEL_*` 的 43 个营 `MotherCountry` 全写着 `US`，`pion_FR_*` 有一批写着
> `CAN` —— 是复制模板没改留下的。
>
> 修正后的做法：**「国家前缀」下拉用名字段 1**（用户原话就是「国家前缀」，
> 成片命名里它是可信的那个）；`MotherCountry` 单独一列显示为「阵营字段」，
> 与名字对不上的行标橙色。两个都摆出来，但不假装等价。
>
> 顺带：`AGF_<hash>` 那 58 个设施的**名称里没有国家段**，所以默认视图只显示
> 部队 —— 否则国家下拉旁边会出现一堆空值，而且「营」这个概念本身也不包含机场。

### 5.2 属性维度：可筛/可改的字段与取值域

按「改起来有没有意义」和「爆炸半径」排：

| 用户概念 | 模块类 | 字段 | 覆盖率 | 取值 | 共享度 | 可批量 |
|---|---|---|---|---|---|---|
| **行动点** | `TActionPointsModuleDescriptor` | `InitialActionPoint` | 2265/2268 | 3 种（12→1676、5→473、4→116） | 47 ★★ | ✅ |
| | | `ActionPointRecoveryPerTurn` | 2265 | — | 47 ★★ | ✅ |
| | | `NbInitialActionsPointsForProducedPawn` | 2265 | — | 47 ★★ | ✅ |
| **移动方式** | `TStrategicMovementModuleDescriptor` | `_ShortDatabaseName` | 2265/2268 | **6 种** | 378 ★★ | ✅ |
| | | `MaxSpeed` / 三个 AP 系数 | | | 378 ★★ | ✅ |
| **部队类型** | `TStrategicLabelModuleDescriptor` → `TBUCK…` | `Values[0..1]` | 2265/2268 | **17 种 × 2** | 1.0 ★ | ✅ |
| **炮兵支援** | `TStrategicBattleModuleDescriptor` | `BattleSupportRadiusInAPCase` | **582/2268** | 6 种 | 206 ★★ | ✅ |
| | | `BattleRole` | 1169/2268 | 3 种（映射待验） | 206 ★★ | ✅ |
| 国家 | `TTypeUnitModuleDescriptor` | `MotherCountry` | 2268/2268 | 14 种 | 1.0 ★ | 建议只读 |
| 疲劳上限 | `TStrategicFatigueModuleDescriptor` | `MaxFatigue` | 2265/2268 | **1 种（全是 8）** | 49 ★★ | 价值低 |
| 分数 | `TDeckModuleDescriptor` | `Score` | 2265 | **1 种（全是 1）** | 1.0 ★ | 价值低 |

**移动方式不是枚举，是指向 6 个共享描述符的引用** —— 所以「换移动方式」
= 换一个 `obj_ref`，**不需要克隆**（比改数值还简单）。这一点要单独走一条路。

### 5.3 界面怎么组织

沿用 `ammo_page` 的三段式，只把「条件区」扩成两层：

```
┌─ 营筛选（名称维度：三个下拉，可搜索）──────────────────────┐
│  国家 [全部 ▾]   上级编制 [全部 ▾]   营 [____________]      │
│                            命中 2265 / 2265 个营            │
├─ 属性条件（多条件 AND，复用 ammo_page 的 ConditionRow）────┤
│  [InitialActionPoint ▾] [等于 ▾] [12]        [+ 添加条件]  │
├─ 结果表 ──────────────────────────────────────────────────┤
│  ☑ 营名            国家  师      行动点  移动方式  图标     │
│  ☑ pion_BEL_11Sq   BEL   —       12      机械化    Infantry │
│  ☑ pion_SOV_…      SOV   1GTA    12      机械化    reco     │
├───────────────────────────────────────────────────────────┤
│  [批量设值…]   命中 128 个，改动 128 个      [保存]        │
└───────────────────────────────────────────────────────────┘
```

两层的分工：

- **名称维度**决定「候选池」，是给人找东西的（我知道 595 侦察营，我要改它）；
- **属性条件**决定「这一批」（所有行动点=12 的装甲营），是给人批量改的。

两者最后**合并成一个 `Query`** —— 名称维度也编译成 `Condition`
（打在 `_country` / `_parent` / `_unit` 三个虚拟字段上），
所以「筛 + 批量改」的链路和弹药页**完全同一条代码**。

---

## 6. 分期实施

### P0 —— 看得见（✅ 已完成）

| # | 任务 | 产出 |
|---|---|---|
| 1 | **只读元数据通道**：`ObjEntry` 加 `meta: dict[str,str]`，收 `DeckIdentifier` / `ClassNameForDebug` / `DescriptorId` 这类被 `_editable` 挡掉但**需要读**的字段 | `schema.py` |
| 2 | **`PawnView` + 投影表**：`ATTRS` 声明「用户概念 → 模块类 → 字段 → 类型 → 显示名」 | 新模块 `ndfedit/ag_view.py` |
| 3 | **专用页骨架**：营列表 + 三维名称筛选 + 属性列（只读） | 新模块 `ndfedit/ag_page.py` |
| 4 | **注册**：`tool_box` 加一项 + `open_ag_editor()`（照 `open_ammo_editor`） | `gui.py` |

**验收（已写成 `tests/test_ag_page.py`，33 项全通过）**

| 检查 | 实测 |
|---|---|
| 部队（营）数 | **2207**；另有 58 个 `AGF` 设施、3 个无标识的 pawn |
| 行动点分布 | **12→1676 / 5→473 / 4→116**（与移交文档 §4 逐位一致）|
| 移动方式 | 6 种：机械化 1618 / 牵引炮 471 / 空中 111 / 直升机 59 / 山地 4 / 直升步兵 2 |
| 部队类型 | 17 种（`RTS_H_` 变体已归一），howitzer 425 / apc 364 |
| 支援半径 | 只 **582** 个营有；10.0→411、-1.0→111 |
| 国家前缀 | **12** 种，SOV 506 / RFA 399 / RDA 238 |
| 名字段数 | 2/3/4/5/6 = **58/244/811/1141/11** |
| 幂等 | 打开页面后 `dirty_count() == 0` |

**实施记录**

| 项 | 结果 |
|---|---|
| 新增 | `ndfedit/ag_view.py`（投影层）、`ndfedit/ag_page.py`（页面）、`tests/test_ag_page.py` |
| 改动 | `schema.py`（`ObjEntry.meta` 只读通道）、`gui.py`（下拉加一项 + `open_ag_editor`） |
| 页面构造 | **0.20 s**（2268 行 × 15 列，含投影与填充） |
| 测试 | 新增 33 项全绿；全量回归 25 / 25 |

**两个只有动手才暴露的问题**

1. **`QTableWidget` 的内置排序会段错误。** 自定义 `QTableWidgetItem` 子类重写
   `__lt__` 后调 `sortItems`，PySide6 6.12 下稳定崩溃（exit 139，
   2268 行 × 15 列复现）。改成「表头点击 → 在 Python 里排好 → 重填」，
   绕开 C++ 回调；代价是每次点击重填一遍（0.2 s）。
   顺带：缺失值的行不论升降序都排在最后 —— 支援半径只有 582/2268 有，
   否则升序时满屏 `—`。
2. **`test_ammo_page` 把「下拉框里只有编辑弹药」写成了精确相等**，
   新增第二个专用页就误报。改成「包含」—— 与「专用页会越来越多」对齐。

> 截图：`build_0.1/ag-page-p0.png`（全量）与 `ag-page-p0-filtered.png`
> （筛 SOV + 按行动点升序）。构建目录不入库。
> 注：离屏渲染（`QT_QPA_PLATFORM=offscreen`）下中文显示为方块 —— 那是
> offscreen 平台没有中文字体，不是界面问题；真实平台正常。

### P1 —— 改得动（✅ 数值属性已完成）

| # | 任务 | 要点 |
|---|---|---|
| 5 | **`set_attr()` 写入器** | 内含 clone-on-write 判定；返回「是否克隆了」 |
| 6 | **换移动方式**（纯 ref 改写） | 不克隆，直接改 `ModulesDescriptors` 里那一条指向 |
| 7 | **批量改**：接上 `query.preview()` / `apply_value()` | 名称维度也编译成 `Condition` |
| 8 | **撤销 + 保存**（✅ 已接线） | 复用 `history` / `writer`；克隆体是一次「追加对象」，已在撤销栈里有路径 |
| 9 | **验证脚本** `tests/test_ag_page.py` | 见下 |

**验收（移交文档 §7 的七条，逐条落成断言）**

1. **读得对** —— 分布与 P0 验收一致；
2. ★ **写隔离** —— 改 595 侦察营的行动点后，**引用同一模块的另外 1637 个营
   逐对象逐属性一字未变**（不是「看起来还在」）；
3. **幂等** —— 不改直接保存 → md5 不变；
4. **结构不退化** —— 改完仍能解码；对象数不减；克隆体是**新 id**、原对象**未被改写**；
5. **数值范围** —— 行动点 ≥ 1；支援半径 ≥ -1；
6. **连接容错** —— 那 3 个没有 `TDeckModuleDescriptor` 的 pawn 显示为
   「无战略属性」，**不崩、也不许拿别的营的数据顶上**；
7. **只改一个文件** —— `Pawn.ndfbin` 落盘且有 `.bak`；**不触碰** `Deck.ndfbin`。

**实施记录**

| 项 | 结果 |
|---|---|
| 新增 | `ndfedit/ag_edit.py`（`set_attr` / `apply_attr` / `module_users` / `repoint_module`）、`tests/test_ag_edit.py` |
| 改动 | `ag_page.py`（属性条件区 + 批量改区 + 未填哨兵）、`gui.py`（`dirtyApplied` 接线） |
| 测试 | `test_ag_edit` **17 / 17**；`test_ag_page` **33 / 33** |

**写入前必须知道的三个实测结论**（都是这一轮踩出来的）

1. **模块对象没有 GUID.** 全文件只有 `TEntityDescriptor` 带 `DescriptorId`
   （`type_id 0x1A`），所有模块类都没有。所以克隆模块**不会**触发换身份逻辑 ——
   模块本来就没有身份，这在语义上是对的，不存在"克隆体和原件共用身份"。
2. **重指向只能走 `RefIndex.repoint`.** `RefListField.nodes()` 返回的是
   **快照**（`[_node_state(r.leaf) for r in self.items]`），改它没有任何效果；
   只改 `object_id` 不改 `class_id` 会让整个文件解不开。必须走
   `repoint(leaf, "obj", new_id)`。
3. **保存会重新压缩.** 解压后只 +54 B，压缩后体积却会变 —— 游戏引擎的 zstd
   编码器与我们不同，`core/writer.py` 早就写明了这一点（984/984 解压正确、
   78 个压缩字节不同）。无改动时走快速路径原样返回，所以幂等仍然成立。

**批量改的策略是「按模块分组」**

| 情况 | 做法 | 理由 |
|---|---|---|
| 命中的营 == 该模块的**全部**用户 | 直接改模块，**0 克隆** | 「把所有 AA 营的 12 改成 15」正是这种，克隆 126 份是纯浪费 |
| 命中的营 < 全部用户 | 每个营克隆一份 | 否则会误伤没命中的营 |

**端到端实测**（柏林 mod，副本上跑）

```
条件「防空半径 ≥ 10」→ 命中 123 个
应用 15.0 → 克隆 123 个（123 < 126，不能直接改共享模块）
保存 → 重载：15.0 × 123、12.0 × 3、6.6 × 2、36.0 × 2
```

没命中的 5 个营一个字节没动。

~~**尚未做的（P1 剩余）**~~ → 换引用那条路已实现，见下一节。

---

### 换引用与补属性（✅ 已完成）

P1 收尾的两件事。它们与「改数值」不是同一种改动，所以各走一条写入路径。

| 路径 | 动的对象 | 典型场景 | 入口 |
|---|---|---|---|
| 改数值 | 模块上的一个标量 | 126 个 AA 营的 12 → 15 | `ag_edit.apply_attr` |
| **换引用** | 模块上的 `strg_ref` / 载体上的成对纹理名 | 某营移动方式 → 空中 | `ag_edit.apply_choice` |
| **补属性** | 克隆描述器 + 追加字段 | 给工兵营加炮兵支援半径 | `ag_edit.grant_attr` |

**换引用：取值必须已经存在**

`strg_ref` 只编码 STRG 表的下标，表里没有的名字**写不进去**；图标也必须是
已存在的成对纹理名。所以可选值一律从数据里现查，不造字符串：

| 属性 | 可选值 | 来源 |
|---|---|---|
| 移动方式 | **6 种** `StrategicMovementDescriptor_*` | 扫 `TStrategicMovementModuleDescriptor` 的 `_ShortDatabaseName`（与 STRG 池一致） |
| 部队类型 | **17 种**（`howitzer` / `AA_air` / `Armor_heavy` …） | 从 2265 个载体的 `Values` 归一（剥掉 `Texture_STRATEGIC_` 与 `RTS_H_` 前缀） |

两条路的安全边界完全不同：

* **移动方式**的模块被 **1618** 个营共用 → 必须 clone-on-write；
  批量时按模块分组，整组命中就不克隆。
* **部队类型**的载体是**每营独占**（2265 个载体各被 1 个营引用）→ 不克隆。
  图标名存在载体的 `Values` 里，而它是**两元素列表**：`[RTS_H_x, x]`，
  两个必须一起改 —— 只改一个会让某个显示场景换成别的图标。

> **`BackgroundTexture` 不在模型里，这是对的。** 它被
> `DENY_SUBSTRINGS` 里的 `"texture"` 挡住了，所以 `ObjEntry.refs` 里没有它
> —— 通用编辑器不该让人把它指向任意对象。专用页因此走一条只读通道：
> `PawnView.carriers` 在投影时就记下载体 id。**没有放宽全局过滤。**
> 载体万一被共用，这里直接报错、不尝试克隆 —— 那条重指向被挡在模型外，
> 撤销栈看不见它，能改但撤不回比不支持更糟。

**补属性：克隆自己的描述器再追加字段**

| 营 | 它们的 battle 描述器 | 有支援半径 |
|---|---|---|
| 炮兵 / 部分合成部队 | 6 个（含 `BattleSupportRadiusInAPCase`） | 582 个营 |
| 防空 | 3 个（含 `AirDenyRadiusInAPCase`） | 130 个营 |
| **步兵 / 装甲 / 工兵…** | 1 个（只有 `CanBeInitialTarget` + `HasZoneOfControl`），被 **1096** 个营共用 | **0** |

所以给一个步兵营加支援半径，只能：克隆它自己的描述器 → `add_field` 追加
`BattleSupportRadiusInAPCase` → 重指向。直接在那个共享描述器上加，等于给
1096 个营一起加上。字段的 `type_id` 由 `field_template()` 从同类里已经有该
字段的对象上取（实测模板值：支援半径 14.0、防空 6.6），不手写线格式。

**界面**

* 批量改的下拉按属性切换控件：数值给数字框，枚举给下拉。
* 枚举下拉首项是**「（未选）」**。不给它的话切过去默认选中第一项（如「空中」），
  按钮立刻可点 —— 手一滑整批就全变空中。与数值框的「（未填）」哨兵同理。
* 「给缺这个属性的营补上」按钮**只在支持补加的属性上出现**（支援半径 / 防空半径）。
* 确认框里写明「最热的描述器被 N 个营共用，直接加会让它们全体多出这个属性」。
* 国家**不在批量改里**：它与编制、名字强耦合，改了只会制造更多
  「名字说一件事、字段说另一件事」。

**验收（`tests/test_ag_choice.py`，67 项）**

```
移动方式：克隆 → 重载后该营是山地；原模块剩 1617 个营仍是机械化
          不存在的名字被拒绝
部队类型：不克隆；Values 两个纹理都换成 AA；howitzer 425→424、AA 131→132
整组命中：6 个山地营全改 → 0 克隆、0 对象新增
补属性：  步兵营 → 克隆 + 加字段 → 582→583；原描述器剩的 456 个营无此字段
          防空营 → 支援 8 与防空 12 共存，防空总数仍 130
          不可补加的字段（行动点）被拒绝
撤销：    三条路径各自退回原样（含「克隆 + 加字段」一步，对象数回原、
          dirty=0、落盘后字段确实消失）
界面：    控件切换、默认「未选」、按钮灰/亮、补按钮只在可补属性上出现
```

> 截图：`build_0.1/ag-page-choice-movement.png`（移动方式下拉）、
> `ag-page-grant.png`（支援半径 + 补上按钮）。构建目录不入库。

---

### 待办 —— ~~给营「赋予它本来没有的属性」~~（✅ 已完成，见上）

> 用户从 NDF 源码确认：**炮兵单位启用「地面支援半径」，防空单位启用
> 「防空拦截」，其它单位的 `TStrategicBattleModuleDescriptor` 上这两个字段
> 都没有。** 想给某个工兵营加支援半径，只能克隆它自己的描述器再追加字段。

这是「改已有字段」之外的第二条路，**与 P1 已完成的写入层不同**：

| | 改已有字段（已完成） | 赋予新属性（已完成） |
|---|---|---|
| 前提 | 模块上已有该字段 | 模块上没有该字段 |
| 做法 | 克隆模块 → 改值 | 克隆模块 → **`add_field` 追加字段** → 改值 → 重指向 |
| 依赖 | `clone_object` | `clone_object` + **`NdfDocument.add_field`**（项目已有，`test_add_field` 39 项覆盖） |

---

### 撤销接线（✅ 已完成）

撤销内核（`history.History`）本来就是通用的，这一节做的只是**把专用页接上它**，
但接的过程中撞出一个内核缺陷。

**发现的问题**

1. **`AgPage` 没有 `refresh()`。** 主窗口的 `_refresh_after_history()` 是
   **按这个名字**去各专用页找回调方法的，找不到就静默跳过。跳过意味着撤销
   之后表格里的数字还停在撤销前那一刻 —— 玩家会以为撤销没生效，然后再改一次。
2. **`History._apply()` 的写入顺序不确定。** `_delta()` 用
   `set(before) | set(after)` 攒出 delta，那个顺序由哈希决定。当一步里
   同时含「克隆对象」和「改指向」时，撤销可能**先砍克隆体、再改引用**，
   而 `set_appended_count()` 会正确地拒绝砍掉一个还被引用着的对象 ——
   于是撤销直接抛 `ValueError`（实测复现）。
   通用编辑器那边克隆与替换是分开的两步，顺序天然正确，所以这个缺陷
   一直没露出来；AG 页的 `apply_attr` 把「克隆 + 重指向 + 改值」压成一次
   提交，才把它逼出来。
3. **主窗口的 Ctrl+Z 在这一页里按不到。** 它是 `Qt.WindowShortcut`，
   作用域是主窗口；AG 页是独立顶层窗口。

**做的改动**

| 文件 | 改动 |
|---|---|
| `ndfedit/history.py` | `_apply()` 按语义排序：**重做**时先物化克隆体再改引用；**撤销**时先把引用指回去、最后才砍克隆体 |
| `ndfedit/ag_page.py` | 加 `refresh()`（重建投影并恢复选中行、清掉过时的改动账）；底部加「撤销 / 重做」按钮；页内自挂 Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z 并转发给主窗口 |
| `ndfedit/gui.py` | `undoRequested` / `redoRequested` 接到主窗口的 `on_undo` / `on_redo`；撤销后由主窗口统一回调 `refresh()` |
| `tests/test_ag_undo.py` | 42 项：数据层（含顺序 bug 的回归）+ 界面层（表格是否重画、按钮状态、条件是否被冲掉） |

**为什么按钮状态取自 `nd.history`**：换文件、保存后重新加载都会换一份
新文档与新历史，按钮直接问文档就很自然地跟着变，不必再同步一份状态。

**验收**

```
撤销后：对象数 18288 → 18285、appended → 0、取值分布逐值回到改前、
        dirty_count → 0、表格从 15 回到 12、按钮 undo 灰 / redo 亮
重做后：全部恢复，对象数回到 18288
两步改动 → 逐级退，退两步才回到原样（不能一步退掉两步）
保存后重新加载 → 历史为空（保存是提交点，不是可撤销操作）
```

---

### 待办 —— 给营「赋予它本来没有的属性」

> 用户从 NDF 源码确认：**炮兵单位启用「地面支援半径」，防空单位启用
> 「防空拦截」，其它单位的 `TStrategicBattleModuleDescriptor` 上这两个字段
> 都没有。** 想给某个工兵营加支援半径，只能克隆它自己的描述器再追加字段。

这是「改已有字段」之外的第二条路，**与 P1 已完成的写入层不同**：

| | 改已有字段（已完成） | 赋予新属性（待做） |
|---|---|---|
| 前提 | 模块上已有该字段 | 模块上没有该字段 |
| 做法 | 克隆模块 → 改值 | 克隆模块 → **`add_field` 追加字段** → 改值 → 重指向 |
| 依赖 | `clone_object` | `clone_object` + **`NdfDocument.add_field`**（项目已有，`test_add_field` 39 项覆盖） |

**实测的字段组合**（11 个 `TStrategicBattleModuleDescriptor` 对象）：

| 字段组合 | 对象数 |
|---|---|
| `BattleRole` + `BattleSupportRadiusInAPCase`（炮兵） | 6 |
| `BattleRole` + `AirDenyRadiusInAPCase`（防空） | 3 |
| `BattleRole` + `CanBeInitialTarget` + `HasZoneOfControl`（普通战斗单位） | 1 |
| `CanBeInitialTarget` + `HasZoneOfControl`（无角色） | 1 |

**实现要点**

1. 目标营的 battle 模块通常是**共享**的（206 个营共用 11 个模块），
   所以「加字段」必须先克隆 —— 直接 `add_field` 会给 200 多个营加上。
2. 追加字段要用 `add_field(entry, field_name, value)`，它按**同类对象的
   字段模板**建条目（`field_template`），所以不需要手写线格式。
3. 新字段的初始值建议取「同类里最常见的那一个」（支援半径 10.0），
   而不是 0 —— 0 半径在游戏里是「有这门能力但完全没用」的怪状态。
4. 界面上要区分「这个营没有这个属性」和「有这个属性但值为 0」：
   前者显示 `—` 且提供「赋予」按钮，后者显示 `0`。

### P2 —— 好用

| # | 任务 | 状态 |
|---|---|---|
| 10 | **图标下拉**：17 种，改时两个 list 元素一起改 | ✅ 已完成（还差「这类图标现在有多少营在用」的角标） |
| 11 | **共享度提示**：编辑共享模块时显示「这个值目前被 N 个营共用」 | 待做（确认框里已有一句「最多一组 N 个营」，但表格里没有常驻提示） |
| 12 | **取值域下拉**：从实测分布生成 | ✅ 已完成（`ag_view.choice_values`） |
| 13 | **派生列**：如「图标 + 移动方式」组合，一眼看出编制是否合理 | 待做 |
| 14 | **缺字段提示**：没有的显示 `—` 不显示 0 | ✅ 已完成；并进一步发展成「补上」按钮 |

---

## 7. 风险与未解

| # | 风险 / 未知 | 严重度 | 处理 |
|---|---|---|---|
| R1 | **`BattleRole` 整数 ↔ 枚举名对应未验证** | 中 | 界面**只显示原始整数值**（1/2/3），不假装知道名字；进游戏实测后再补标签 |
| R2 | **`-1.0` 支援半径的语义**（111 个营） | 中 | 按原值显示，不解释成「无支援」；下拉里保留 `-1.0`，并标注「含义待验」 |
| R3 | **代码内验证过的 ≤ 游戏内生效** | 中 | 与项目的既有态度一致：本轮只做到「编码往返正确」，**真机验证仍是 P0 之外的头号待办** |
| R4 | ~~`clone_object()` 在模块对象上的行为未查~~ | — | ✅ **已查**：全文件只有 `TEntityDescriptor` 带 `DescriptorId`（0x1A），所有模块类都没有。所以克隆模块**不会**触发换身份逻辑，`last_clone_guid_fields` 是空的 —— 语义上正确，模块本来就没有身份 |
| R5 | **图标贴图是否存在于该 mod** | 低 | 只改引用名，不校验资源存在；玩家自己承担 |
| R6 | **`MaxFatigue` / `Score` 全是同一个值** | 低 | P2 才做，且界面上标注「改它等于引入差异」 |
| R7 | **mod 间互相覆盖** `Pawn.ndfbin` | 低 | 单文件编辑天然如此，与现有行为一致；不需要额外处理 |

---

## 8. 移交资产的处理

| 资产 | 建议 |
|---|---|
| 移交文档本身 | 已在 `doc/`（名字不含「勘查报告」，**会进公开仓库**）。它是项目文档，保留 |
| `probe_pawn.py`（在 NdfbinDecksEditor） | **本轮没有搬**。实测已用本项目自己的读写链重做了全部勘察，脚本不需要。若要留档，建议按 `tools/_src.py` 的发现式路径**重写一份**（`tools/probe_pawn.py`），而不是直接拷（它读的是硬编码部署目录） |
| 原立项文档 | 不搬 |

---

## 附录：本轮实测的完整数据来源

所有数字来自一次脚本运行（柏林 mod，`Pawn.ndfbin`，18,285 对象 / 39 类）。

```
TEntityDescriptor                     2268
TTypeUnitModuleDescriptor             2269   ← 比 pawn 多 1（模板对象）
TTagsModuleDescriptor                 2269
TStrategicLabelModuleDescriptor       2265   ← 每营一个（独有）
TPawnUIModuleDescriptor               2265
TDeckModuleDescriptor                 2265
TBUCK…TUIValueTextureNameFrom…        2265   ← 装图标的载体（独有）
TApparenceModuleDescriptor            2265
TActionPointsModuleDescriptor           48   ← 被引 2265，共享 47 ★★
TStrategicFatigueModuleDescriptor       46   ← 被引 2265，共享 49 ★★
TStrategicBattleModuleDescriptor        11   ← 被引 2265，共享 206 ★★
TFlagsModuleDescriptor                   7   ← 共享 324 ★★
TStrategicMovementModuleDescriptor       6   ← 被引 2265，共享 378 ★★
TPositionModuleDescriptor                5   ← 共享 454 ★★
TInfluenceMapModuleDescriptor            3   ← 共享 699 ★★
…（其余为共享度 ≥ 1000 的「全表通用」模块）
```

**共享度最高的一批**（必须克隆）：
`TVisibilityModuleDescriptor` / `TLinkTeamModuleDescriptor` /
`TEffectApplierModuleDescriptor` 各为 **1 个对象 / 2268 次引用**。

**独有的一批**（可原地改）：
`TApparenceModuleDescriptor` / `TStrategicLabelModuleDescriptor` /
`TDeckModuleDescriptor` / `TPawnUIModuleDescriptor` / `TTypeUnitModuleDescriptor`
均为 **2265~2269 对象 / 同数量引用**。
