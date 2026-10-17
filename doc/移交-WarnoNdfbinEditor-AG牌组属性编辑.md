# AG 牌组属性编辑 —— 移交 Warno Ndfbin Editor

> **文件性质**：项目间沟通文件（移交说明），不是本项目的勘查报告。
> **移交方**：NdfbinDecksEditor ｜ **接收方**：Warno Ndfbin Editor
> **日期**：2026-10-16 ｜ **状态**：数据勘查已完成，功能未实现（本项目已取消立项）

---

## 0. 为什么移交

这个需求是「编辑将军模式牌组在**战略层**的属性」——部队类型、移动方式、行动点、
炮兵支援覆盖范围。

它最初在 NdfbinDecksEditor 立项，勘查完之后的结论是**放错项目了**：

| | NdfbinDecksEditor 在管的事 | 这个功能实际要做的事 |
|---|---|---|
| 文件 | `Gen/NDF/GFX/Deck.ndfbin` 一个文件 | **另一个文件** `Pawn.ndfbin` + 与 deck 的跨文件关联 |
| 对象 | 编制：连/排/槽位/装备包 | **单位属性**：一组标量（数字、枚举、引用） |
| 约束 | `Σ(Number) == len(DeckPackList)`、窗口必须铺满 | 无此类全局约束，但**模块被大量共享** |

也就是说：这不是"牌组编制编辑"，而是**通用 NDF 属性编辑**。Warno Ndfbin Editor 作为
通用编辑器，这件事属于它的能力范围；放在牌组编辑器里会逼它变成半个通用编辑器。

**本文档的目的**：把已经勘查清楚的数据事实、唯一的安全约束、验收标准一次交清，
让对方不必重做勘查。

---

## 1. 需求（用户视角）

选中一个将军模式牌组（营），能改：

1. **部队类型** —— 战略地图上的兵种图标（侦察 / 步兵 / 装甲 / 火炮 / 防空 …）
2. **移动方式** —— 机械化 / 牵引炮 / 空中 / 直升机 / 山地
3. **行动点** —— 初始行动点、每回合恢复、给生产兵的点数
4. **炮兵支援覆盖范围** —— 以行动点为单位的支援半径

顺带可做（同机制，零额外成本）：战斗角色、防空拦截半径、隐蔽加成、疲劳上限、
影响强度、营的分数。

---

## 2. 数据契约：属性在哪

### 2.1 ★★ 首先排除一个弯路

**这四个属性一个都不在 `Deck.ndfbin` 里。** 那个文件的**全部**类与属性只有：

| 类 | 属性 |
|---|---|
| `TDeckDescriptor` | DeckCombatGroupList · DeckDivision · DeckIdentifier · DeckName · DeckPackList |
| `TDeckCombatGroupDescriptor` | IsHQ · Name · SmartGroupList |
| `TDeckSmartGroupDescriptor` | IsHQ · Name · PackIndexUnitNumberList |
| `TDeckPackDescriptor` | ExperienceLevel · Number · TransporterDescriptor · UnitDescriptor |
| `TDeckGroupRules` | 6 个上限值 |

这是**编制**，不含任何战略属性。别在 `Deck.ndfbin` 里找。

### 2.2 它们在 `Pawn.ndfbin`

```
<mod>/Gen/NDF/GFX/Pawn.ndfbin        ← 柏林 mod 实测 212,722 B / 18,285 对象 / 2,274 导出
```

每个将军模式营在里面有一个 `TEntityDescriptor`，**属性以「模块」形式挂在它身上**：

```
Descriptor_Unit_pion_SOV_1GTA_595Reco  is TEntityDescriptor
(
    ...
    ModulesDescriptors = [
        ...,
        TActionPointsModuleDescriptor( InitialActionPoint = 12 ... ),   ← 行动点
        ~/StrategicMovementDescriptor_mecanized,                        ← 移动方式
        TStrategicLabelModuleDescriptor( BackgroundTexture = ... ),     ← 部队类型
        TStrategicBattleModuleDescriptor( BattleSupportRadiusInAPCase = 10.0 ... ),  ← 炮兵支援
    ]
)
```

**编译后的形态**：`ModulesDescriptors` 是一串**纯 `obj_ref`**（595 侦察营 28 条，实测全部
是 `obj_ref`，没有命名引用）。模块对象是**顶层对象**，不是嵌套结构。
→ **改一个属性 = 改某个模块对象的标量属性**（或换一个 obj_ref）。

### 2.3 四个属性的落点

| 用户概念 | 字段 | 宿主模块类 | 线格式类型 |
|---|---|---|---|
| 部队类型 | `BackgroundTexture` → 指向一个 `Values` 字符串表 | `TStrategicLabelModuleDescriptor` | `obj_ref` |
| 移动方式 | `_ShortDatabaseName` + `MaxSpeed` + 地形/道路 AP 系数 + `ConsumeAPOnMove` | `TStrategicMovementModuleDescriptor` | `strg_ref` / `float32` / `bool` |
| 行动点 | `InitialActionPoint` · `ActionPointRecoveryPerTurn` · `NbInitialActionsPointsForProducedPawn` | `TActionPointsModuleDescriptor` | `float32` / `int32` |
| 炮兵支援覆盖范围 | `BattleSupportRadiusInAPCase` | `TStrategicBattleModuleDescriptor` | `float32` |

> 完整的 30 个模块类 × 字段 × 类型清单见 **附录 A** —— 那张表可以直接拿去建通用属性编辑器。

### 2.4 与牌组的连接

```
牌组标识  pion_SOV_1GTA_595Reco
   ├─ Deck.ndfbin :  DeckIdentifier        = 'pion_SOV_1GTA_595Reco'
   └─ Pawn.ndfbin :  Descriptor_Unit_pion_SOV_1GTA_595Reco
                     TDeckModuleDescriptor.DeckIdentifier = 'pion_SOV_1GTA_595Reco'
```

**实测对应率 2265 / 2266 = 100.0%**（柏林 mod）。
唯一落空的是 `test_deck_avion`（测试用飞机营，pawn 表里没有）。
反向多出 3 个：`AGF_Airfield_*` / `TestAirport` / `TestDepotRavitaillement`（机场与补给站，不是营）。

> **建议用 `TDeckModuleDescriptor.DeckIdentifier` 作为连接键**，别靠导出名的字符串拼接
> （编译后的导出名带 `pion_` 前缀，与牌组标识不同形）。

---

## 3. ★★ 唯一的安全边界：模块被编译器去重合并

**这是整件事唯一会出大事故的地方。**

编译器把**内容相同**的模块合并成**同一个对象**。所以一个营指向的模块，绝大多数
**不是它自己的**：

| 属性 | 对象数 | 承载的营 | **平均共享** |
|---|---|---|---|
| 隐蔽加成（`TVisibilityModuleDescriptor`） | 1 | 2268 | **2268** |
| 移动方式（`TStrategicMovementModuleDescriptor`） | 6 | 2265 | **378** |
| 战斗角色 / 支援半径（`TStrategicBattleModuleDescriptor`） | 11 | 2265 | **206** |
| 疲劳上限 | 46 | 2265 | 49 |
| 行动点（`TActionPointsModuleDescriptor`） | 48 | 2265 | **47** |
| —— 其中 `InitialActionPoint = 12` 那一个对象 | 1 | — | **1638** |
| **部队类型图标**（`TStrategicLabelModuleDescriptor`） | **2265** | 2265 | **1.0（独有）** |
| **分数**（`TDeckModuleDescriptor`） | **2265** | 2265 | **1.0（独有）** |
| 国家 / 阵营（`TTypeUnitModuleDescriptor`） | 2269 | 2268 | 1.0（独有） |

**具体到 595 侦察营**：行动点模块被引用 **1638** 次、移动方式 **1618** 次、战斗角色 **457** 次。

> **原地改一个数字 = 一次性改掉 1638 个营的行动点。**

### 3.1 唯一安全的改法：clone-on-write

```
改「共享模块」的属性：
    1. 追加一个新的同类型模块对象（复制原对象的属性，写入新值）
    2. 只把【这一个营】的 ModulesDescriptors 里那条 obj_ref 指向新对象
    → 其余 1637 个营仍指向原对象，一个字节未动

改「独有模块」的属性（部队类型 / 分数 / 国家）：
    直接原地改，零爆炸半径
```

判断依据就是上表的**平均共享因子**（对象数 vs 引用数）——这也是本项目的
`tools/probe_pawn.py attr` 每条都会打印那个数、并标 ★/★★ 的原因。

> 若接收方已有"共享引用"的概念（例如装备包、材质），这条纪律是同一套。

---

## 4. 取值域速查（柏林 mod 实测，供下拉框用）

**行动点**（2265 个营全部有值）

| 值 | 营数 |
|---|---|
| 12 | 1676 |
| 5 | 473 |
| 4 | 116 |

**移动方式**（6 种，覆盖全部 2265 营）

| 描述符名 | 营数 | MaxSpeed | 普通地形 AP 系数 | 减速地形系数 | 道路系数 |
|---|---|---|---|---|---|
| `mecanized` | 1618 | 177 | 2.0 | 4.0 | 1.0 |
| `howitzer` | 471 | 424 | 2.0 | 4.0 | 1.0 |
| `aerial` | 111 | 1767 | 1.0 | 2.0 | 0.8 |
| `helico` | 59 | 177 | 1.0 | 1.0 | 1.0 |
| `montagne` | 4 | 177 | 2.0 | 2.0 | 1.0 |
| `helico_inf` | 2 | 177 | 1.5 | 1.5 | 1.5 |

> ★ **移动方式不是枚举值，是指向 6 个共享描述符之一的引用** —— 换移动方式 = 换一个 obj_ref，
> 比"克隆"更简单（除非要改速度本身）。

**炮兵支援覆盖范围**（单位 = 行动点 AP；582 个营写了）

| 值 | 营数 |
|---|---|
| 未写 | 1683 |
| 10.0 | 411 |
| -1.0 | 111 |
| 11.5 | 36 |
| 14.0 | 12 |
| 17.0 | 9 |
| 15.0 | 3 |

**交叉验证**：471 个营用 `howitzer` 移动，而 411+36+12+9+3 = **471** 个营有正的支援半径 ——
两边完全对上，说明"炮兵 = howitzer 移动 + 支援半径"这条读法成立。

**部队类型图标**：17 种在用（howitzer 425 / apc 364 / Armor_heavy 320 / ifv 241 /
Infantry 143 / Armor 140 / AA 131 / HQ 129 / reco 83 / assault 61 / hel 59 /
Support_air 48 / mlrs 48 / AA_air 45 / ATGM_air 11 / AT 10 / SEAD_air 7）。
取值全集在官方明文源 `GameData/Generated/Gameplay/Unit/Strategic/Units.ndf`（26 个）
+ `AirplaneUnits.ndf`（空中那几种）。

**战斗角色**：源码枚举 3 个 —— `AuxiliarySupport` / `Fighter` / `GroundSupport`；
编译后是整数 1 / 2 / 3（1096 个营未写、587 = 1、471 = 3、111 = 2）。

---

## 5. 二进制层：已确认可行，但有前提

本项目用自研的解码器实测：

| 项 | 结果 |
|---|---|
| 解码 | 0.49 s（18,285 对象） |
| **往返无损** | **逐字节相同**、主体相同 |
| 追加新对象 | 有可用的「仅追加」写入口，扩展 CLAS/PROP/CHNK 后自动复验对象图 |
| 重要前提 | 本项目把它纳入了**每次回归**的往返验证，所以"能读能写"是**持续被证明**的，不是一次性结论 |

> **对接收方的要求**：请先用你们的读写链对 `Pawn.ndfbin` 做一次
> **decode → encode → 逐字节比对**。通不过就先别做属性编辑 ——
> 这个文件的嵌套深度和对象数都比 `Deck.ndfbin` 大，不能假设"另一个文件能过，这个也能过"。

另外：模块对象上保留了源码名 `_ShortDatabaseName`（40 个对象，
如 `StrategicMovementDescriptor_mecanized`、`InfluenceDataModuleDescriptor`）——
**界面可以直接显示可读名字**，不必给用户看对象 id。

---

## 6. 建议的实现分层

| 层 | 职责 | 要点 |
|---|---|---|
| **读** | 打开 `Pawn.ndfbin`，按 `DeckIdentifier` 建索引 | 与 deck 解耦；缺文件时降级为空，不抛 |
| **模型** | `pawn → [(module_class, module_obj, shared_factor)]` | `shared_factor` 必须算出来并**随模型带出去**，界面和写入器都要用它 |
| **写** | `set_attr(pawn, module_class, field, value)` | 内含 clone-on-write 判定；返回"是否克隆了" |
| **UI** | 选中营 → 战略属性面板 | 图标/移动方式用下拉（取值域见 §4），行动点/半径用数值框 |
| **保存** | ★ **要写两个文件** | `Deck.ndfbin` 与 `Pawn.ndfbin` 各自 `.bak`；界面上说清改的是哪个文件 |
| **校验** | 见 §7 | 数值范围 + 模块类与字段匹配 + 共享度提示 |

---

## 7. 验收标准（可直接写成断言）

1. **读得对**：能列出每个营的 4 个属性；分布与 §4 的数字一致
   （例如 `InitialActionPoint = 12` 的营数 = 1676）。
2. **★ 写隔离**：改某营的行动点后，**引用同一模块的另外 1637 个营一字未变**
   —— 逐对象逐属性比对，不是"看起来还在"。
3. **幂等**：不改任何东西直接保存 → **逐字节相同**。
4. **结构不退化**：改完后仍能解码；对象数、营数不减少；
   被克隆出来的模块对象是**新 id**，原对象**未被改写**。
5. **数值范围**：行动点 ≥ 1；支援半径 ≥ -1（或按 §8 的实测结论）。
6. **连接容错**：找不到对应 pawn 的营（实测有 1 个）在界面上表现为"无战略属性"，
   **不是崩溃**，也不许静默拿另一个营的数据顶上。
7. **双文件**：改 pawn 后两个文件都落盘且各有 `.bak`；只改 deck 时**不**触碰 `Pawn.ndfbin`。

---

## 8. ⚠️ 必须实测确认的未知（别当已知用）

1. **`BattleRole` 整数 ↔ 名字的对应**：源码是 3 个枚举名，编译后是 1/2/3，
   **对应关系未验证**。（"471 个 howitzer ≈ 471 个 = 3"，可推测 3 = GroundSupport，
   但这是推测，不是结论。）
2. **支援半径 `-1.0` 的语义**（111 个营）—— 是"无支援"还是"禁用"？界面怎么呈现要先定。
3. **行动点的量纲**：12 / 5 / 4 是"每回合点数"，但**与地图距离、地形系数的换算关系未验证**。
   改大改小在游戏里的实际手感需要试。
4. **26 种图标里哪些适合开放给玩家**：`RTS_H_` 前缀变体的语义未知，且图标牵涉
   贴图资源是否存在于该 mod。
5. **`Pawn.ndfbin` 是整份替换**：`Test` mod 只有 `Deck.ndfbin`（依赖游戏本体那份），
   柏林 mod 自带 `Pawn.ndfbin`。改它只影响本 mod ✓，但**两个 mod 都改这个文件时互相覆盖**。
6. **`Config.ini` 的 `GFX/Pawn=<hash>` 兼容哈希**（标注"mod 兼容性检查用，勿改"）：
   实测它**既不等于该文件的 md5 也不等于 sha256**，所以无法从哈希形态判断改完要不要同步。
   同理 `DeckFormatVersion`。**只能进游戏实测。**
7. **`Score` 目前 2265 个营全是 1** —— 改它等于"引入差异"而不是"修正不同"。
   若要用它做平衡，得先查清游戏读它的地方。

---

## 9. 移交资产清单

| 资产 | 位置 | 处理建议 |
|---|---|---|
| 本文档 | `NdfbinDecksEditor/doc/移交-WarnoNdfbinEditor-AG牌组属性编辑.md` | **原件在本项目 `doc/`（不进公开仓库），请拷走** |
| 探查工具 `probe_pawn.py` | `NdfbinDecksEditor/tools/probe_pawn.py` | **建议一并移交**。它不被本项目任何模块引用，移走不影响本项目回归；子命令见 §10 |
| 明细数据 | 由 §10 的命令一键复现 | 不必单独传数据文件 |
| 本项目原立项 | TODO「阶段 11」 | 已**取消**（保留结论备查） |

---

## 10. 复现命令（`probe_pawn.py`）

```bash
python probe_pawn.py classes     # 文件里有什么、各模块共享得多厉害
python probe_pawn.py join        # 牌组 ↔ pawn 的对应率
python probe_pawn.py attr        # 四个属性的分布 + 共享因子（★/★★ 标注）
python probe_pawn.py icons       # 部队类型（战略图标）分布
python probe_pawn.py names       # 哪些模块保留了源码名
python probe_pawn.py pawn SOV_1GTA_595Reco    # 一个营的 28 个模块逐条列出
```

它读的是 mod 部署目录里的 `Gen/NDF/GFX/Pawn.ndfbin`。
**不要**去读官方 `Mods/ModData/base.zip` 里的 `Units.ndf` ——
那是**源码**，模块是**内联**的、没有去重；编译后才会合并成共享对象。两者结构不同，
用源码理解语义、用编译产物写代码。

---

## 附录 A：AG pawn 用到的全部模块 × 字段 × 类型

★ = 独有（可原地改）｜★★ = 共享（必须克隆再重指向）。共享列 = 引用数 ÷ 对象数。

| 模块类 | 对象 | 引用 | 共享 | 字段（名称:线格式类型） |
|---|---|---|---|---|
| `TLinkTeamModuleDescriptor` | 1 | 2268 | **2268** ★★ | `_ShortDatabaseName:strg_ref` |
| `TEffectApplierModuleDescriptor` | 1 | 2268 | **2268** ★★ | `_ShortDatabaseName:strg_ref` |
| `TVisibilityModuleDescriptor` | 1 | 2268 | **2268** ★★ | `UnitConcealmentBonus:float32` |
| `TStateEngineUnitModuleDescriptor` | 1 | 2265 | 2265 ★★ | `_ShortDatabaseName:strg_ref` |
| `TSelectionModuleDescriptor` | 1 | 2265 | 2265 ★★ | `IsSelectable:bool` `IsHighlightable:bool` `HasSelectionShader:bool` `HasHighlightShader:bool` |
| `TStrategicSequenceModuleDescriptor` | 1 | 2265 | 2265 ★★ | `_ShortDatabaseName:strg_ref` |
| `TIAStratZoneIndexModuleDescriptor` | 1 | 2265 | 2265 ★★ | `_ShortDatabaseName:strg_ref` |
| `TInfluencePositionModuleDescriptor` | 1 | 2157 | 2157 ★★ | `_ShortDatabaseName:strg_ref` |
| `TInfluenceDataModuleDescriptor` | 1 | 2157 | 2157 ★★ | `_ShortDatabaseName:strg_ref` |
| `TStrategicIdleStatusModuleDescriptor` | 1 | 2154 | 2154 ★★ | `_ShortDatabaseName:strg_ref` |
| `TActionPointsReachModuleDescriptor` | 1 | 2154 | 2154 ★★ | `_ShortDatabaseName:strg_ref` |
| `TIALinkToGroupModuleDescriptor` | 1 | 2154 | 2154 ★★ | `_ShortDatabaseName:strg_ref` |
| `TReverseScannerWithIdentificationDescriptor` | 2 | 2268 | 1134 ★★ | `IdentifyBaseProbability:float32` `VisibilityRuleDescriptor:trans_ref` |
| `TStrategicPositionModuleDescriptor` | 2 | 2268 | 1134 ★★ | `KernelRadius:int32` |
| `TOrderableModuleDescriptor` | 2 | 2154 | 1077 ★★ | `UnlockableOrders:list` |
| `TInfluenceMapModuleDescriptor` | 3 | 2098 | 699 ★★ | `InfluenceStrength:float32` `MinimumInfluenceStrength:float32` `StrengthDecayPerSecond:float32` `PreventsDecayInZone:bool` |
| `TPositionModuleDescriptor` | 5 | 2268 | 454 ★★ | `InitialCollisionPlane:int32` `InGeoDb:bool` `ClampInWorld:bool` `ClampOutMap:bool` |
| `TStrategicMovementModuleDescriptor` | 6 | 2265 | **378** ★★ | `MaxSpeed:float32` `NormalTerrainAPConsumptionModifier:float32` `SpeedReducerTerrainAPConsumptionModifier:float32` `RoadAPConsumptionModifier:float32` `ConsumeAPOnMove:bool` |
| `TFlagsModuleDescriptor` | 7 | 2268 | 324 ★★ | `InitialFlagSet:list` |
| `TStrategicBattleModuleDescriptor` | 11 | 2265 | **206** ★★ | `BattleRole:int32` `BattleSupportRadiusInAPCase:float32` `AirDenyRadiusInAPCase:float32` `CanBeInitialTarget:bool` `HasZoneOfControl:bool` |
| `TOrderConfigModuleDescriptor` | 1 | 111 | 111 ★★ | `_ShortDatabaseName:strg_ref` |
| `TStrategicAerialModuleDescriptor` | 1 | 111 | 111 ★★ | `_ShortDatabaseName:strg_ref` |
| `TStrategicFatigueModuleDescriptor` | 46 | 2265 | 49 ★★ | `MaxFatigue:uint32` |
| `TActionPointsModuleDescriptor` | 48 | 2265 | **47** ★★ | `InitialActionPoint:float32` `ActionPointRecoveryPerTurn:int32` `NbInitialActionsPointsForProducedPawn:int32` |
| `TScannerConfigurationDescriptor` | 1 | 3 | 3 ★★ | `OpticalStrengths:map_list` `VisionRangesGRU:map_list` |
| `TWargameBuildingModuleDescriptor` | 1 | 3 | 3 ★★ | `BoundingBoxMinInLBU:trans_ref` `BoundingBoxMaxInLBU:trans_ref` |
| `TStrategicAirportModuleDescriptor` | 1 | 2 | 2 ★★ | （无字段） |
| `TApparenceModuleDescriptor` | 2265 | 2265 | **1.0** ★ | `Depiction:trans_ref` `ReferenceMesh:trans_ref` `PickableObject:bool` `BlackHoleKey:strg_ref` |
| `TStrategicLabelModuleDescriptor` | 2265 | 2265 | **1.0** ★ | `BackgroundTexture:obj_ref` ← 部队类型 |
| `TDeckModuleDescriptor` | 2265 | 2265 | **1.0** ★ | `DeckIdentifier:strg_ref` `Score:uint32` |
| `TPawnUIModuleDescriptor` | 2265 | 2265 | **1.0** ★ | `NameToken:loc_hash` `ProdMenuTexture:strg_ref` |
| `TTypeUnitModuleDescriptor` | 2269 | 2268 | **1.0** ★ | `Coalition:int32` `MotherCountry:strg_ref` |
| `TTagsModuleDescriptor` | 2269 | 2268 | **1.0** ★ | `TagSet:list` |

> 数据来源：柏林 mod（`WarnoAGF_berlin_1989`）的 `Pawn.ndfbin`，2,265 个将军模式营。
> 其中"1 个对象 / 2268 次引用"这类极值说明该模块全表通用；
> 而 `TApparenceModuleDescriptor` 等四类是**每营一份**，含各自的贴图与标识。

---

## 附录 B：一个营的 28 个模块（595 侦察营，样例）

供实现时对照"一个 pawn 到底挂了多少东西"：

```
 0 TTypeUnitModuleDescriptor                       被引 1      MotherCountry=SOV, Coalition=1
 1 TFlagsModuleDescriptor                          2157
 2 TPositionModuleDescriptor                       2265
 3 TLinkTeamModuleDescriptor                       2268
 4 TTagsModuleDescriptor                           1
 5 TApparenceModuleDescriptor                      1
 6 TInfluenceMapModuleDescriptor                   2095
 7 TInfluencePositionModuleDescriptor              2157
 8 TInfluenceDataModuleDescriptor                  2157
 9 TStateEngineUnitModuleDescriptor                2265
10 TSelectionModuleDescriptor                      2265
11 TStrategicIdleStatusModuleDescriptor            2154
12 TEffectApplierModuleDescriptor                  2268
13 TStrategicLabelModuleDescriptor                 1       ← 部队类型（独有）
14 TOrderableModuleDescriptor                      2024
15 TDeckModuleDescriptor                           1       ← DeckIdentifier / Score（独有）
16 TStrategicBattleModuleDescriptor                457     ← BattleRole（共享）
17 TVisibilityModuleDescriptor                     2268
18 TReverseScannerWithIdentificationDescriptor     2265
19 TActionPointsModuleDescriptor                   1638    ← 行动点（共享，被 1638 个营引用）
20 TActionPointsReachModuleDescriptor              2154
21 TStrategicMovementModuleDescriptor              1618    ← 移动方式（共享，mecanized）
22 TPawnUIModuleDescriptor                         1
23 TIALinkToGroupModuleDescriptor                  2154
24 TStrategicPositionModuleDescriptor              2266
25 TStrategicSequenceModuleDescriptor              2265
26 TStrategicFatigueModuleDescriptor               2220
27 TIAStratZoneIndexModuleDescriptor               2265
```
