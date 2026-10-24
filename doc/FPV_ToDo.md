# FPV_ToDo —— D1 最小验证包

> **这份文档是什么**：把「给 FPV 无人机加运输模块、验证它能被载具装载」这个假设  
> 拆成 3 个互不依赖的验证阶段，每阶段给出**改哪个文件、改哪个字段、填什么值、  
> 怎么判断成功或失败**。
>
> 所有字段、取值、先例都来自 `base.zip` 的原版 `.ndf` 实测，不是推测。  
> 未验证的地方明确标了「未验证」。
>
> 适用：任何 WARNO mod 项目。不依赖特定编辑器，用 Ndfbin Editor 或官方 mod 工具都行。

---

## 1. 先看结论：D1 的未知只有一个

|                       | 状态                                                                   |
| --------------------- | -------------------------------------------------------------------- |
| 空中单位能不能挂武器            | ✅ 已证：`Flying` 单位里 **510 个**带武器描述符                                    |
| 射程能不能压到极短             | ✅ 已证：947 个弹药里**最小 150 GRU**，`MinimumRangeGRU` 有 198 个用 0             |
| 开火后能不能自动做一件事          | ✅ 已证：`Capacite_Artillerie_tag_on_shoot` 是 `automatic` 触发器 + 开火后条件    |
| 能不能让单位自己消失            | ✅ 已证：`UnitEffect_Destruction` = 一条 `TUnitEffectRemoveUnitDescriptor` |
| **Flying 单位能不能被运输装载** | ❌ **唯一未知**：637 个 Flying 单位里 **0 个**带运输模块，原版零先例                       |

所以 D1 的全部工作量，就是回答最后这一行。**其余都是已知路径，不需要探索。**

### 为什么这个问题必须实测

`TTransportableModuleDescriptor` 只有 5 个字段（`TransportedTexture` / `TransportedSoldier` /  
`NbSeatsOccupied` / `TimeToLoad` / `IsTowable`），**没有任何「只限地面」的开关**。  
装载资格是靠载具的 `TransportableTagSet` 做**标签字符串匹配**的。

也就是说：数据层面没有任何东西阻止一架飞机被装进车里。引擎层面有没有别处的硬校验  
（比如装载时检查 pathfind 类型），**数据回答不了**。这就是必须进游戏的原因。

---

## 2. 验证总览：三个阶段，别一次全做

| 阶段    | 验证什么            | 改几个 `.ndf` | 改动量        | 预计    | 依赖        |
| ----- | --------------- | ---------- | ---------- | ----- | --------- |
| **A** | 开火 → 自己消失       | 1          | 改 1 个引用    | 半天    | 无         |
| **B** | Flying 单位能不能被装载 | 1          | 改 2 处      | 半天    | 无         |
| **C** | 组装成 FPV 本体      | 5          | 新增 5 个对象   | 1~2 天 | A + B 都通过 |

**A 和 B 互不依赖，可以并行做，也可以只做一个先看结果。**

阶段 A 用**自行火炮**验证，与飞机无关 —— 因为「技能 → 开火后触发 → RemoveUnit」这条链
跟移动类型没关系，用炮验最快、最省事、噪音最小。

阶段 B 只改 Pero 一个单位，不加任何新对象。

只有 A、B 都过了，才值得做 C（C 要新增对象、横跨五个 `.ndf`，成本高得多）。

> **路径约定**：下文所有 `.ndf` 路径都相对于
> `D:\Steam\steamapps\common\WARNO\Mods\<你的mod>\`
> 改的是**源码 `.ndf`**，不是编译后的 `.ndfbin`。工程结构见 5-C1。

---

## 3. 阶段 A：开火后自己消失

### A1. 为什么用自行火炮验

原版 `Capacite_Artillerie_tag_on_shoot` 挂在**所有自行火炮**上（`2S1_Gvozdika_SOV`、  
`2S19_MstaS_SOV`、`2S23_Nona_SVK_SOV` 等），它的语义正是：

```
Trigger      = automatic            ← 不需要玩家点，条件满足就触发
TargetMySelf = True                 ← 作用于自己
TargetEffect = ~/UnitEffect_Ajoute_Tag_CBR_cible   ← 把自己打一个 tag
Conditions   = [~/ConditionTimeAfterShot_15000]    ← 条件是「开火之后」
```

**把 `TargetEffect` 换成 `~/UnitEffect_Destruction`，这门炮开火后就会把自己删掉。**

这是改**一个引用**。不新增对象、不碰 GUID、不碰跨文件引用。

### A2. 改哪里

| 文件                                  | 对象                                 | 字段             | 原值                                  | 改成                         |
| ----------------------------------- | ---------------------------------- | -------------- | ----------------------------------- | -------------------------- |
| `GameData\Generated\Gameplay\Gfx\CapaciteList.ndf` | `Capacite_Artillerie_tag_on_shoot` | `TargetEffect` | `~/UnitEffect_Ajoute_Tag_CBR_cible` | `~/UnitEffect_Destruction` |

就这一处。

`UnitEffect_Destruction` 在同一个**编译单元**里 —— `CapaciteList.ndf` 和 `EffetsSurUnite.ndf`
都编译进 `EffectCapacity.ndfbin`（实测那里有 1 个 `TUnitEffectRemoveUnitDescriptor`、
226 个 `TEffectsPackDescriptor`）。内容是：

```
export UnitEffect_Destruction is TEffectsPackDescriptor
(
    DescriptorId       = GUID:{c61861fb-8693-48ab-9227-c001299c3443}
    NameForDebug       = 'Destruction'
    EffectsDescriptors = [
        TUnitEffectRemoveUnitDescriptor(),
    ]
)
```

**不需要新建它，直接引用。**

### A3. 进游戏怎么验

1. 开局，出一门 `2S1_Gvozdika_SOV`（或用现有部队里的任意自行火炮）
2. 让它打一炮（对任何目标）
3. 观察：**它自己消失了没有**

### A4. 判定表

| 现象                  | 结论                               | 下一步                          |
| ------------------- | -------------------------------- | ---------------------------- |
| 开炮后炮立刻消失            | ✅ **链路通**，`Duration` 可能是 0 或立即生效 | 进阶段 C                        |
| 开炮后约 15 秒消失         | ✅ **链路通**，`Duration` = 延迟秒数      | 进阶段 C，按需调 `Duration`         |
| 炮消失但残骸/爆炸都没有        | ✅ 机制通，只是观感缺失                     | 后面挂 `DeathExplosionAmmo` 补爆炸 |
| 开炮后什么都没发生           | ❌ 技能没触发                          | 见下方排查                        |
| 炮没消失，反而所有同型号炮都变强/变弱 | ❌ 你改错了对象（改到了效果包内容）               | 回滚，只改 `TargetEffect` 这一行     |

**如果什么都没发生**，先排查这三点（按可能性排序）：

1. `Trigger` 还是不是 `~/CapacityTriggerType_automatic`
2. `TargetMySelf` 还是不是 `True`（改成 `False` 就变成对目标生效，炮不会消失）
3. `Conditions` 里 `ConditionTimeAfterShot_15000` 还在不在

> **未验证的语义**：`TConditionTimeAfterShot` 只有 `Duration` 一个参数（炮兵版是 15）。  
> 它到底是「开火后 N 秒内持续生效」还是「开火后等 N 秒」，数据看不出来 ——  
> 但**两种语义对 FPV 都能用**：前者开火即爆，后者延迟爆。判定表的第 1、2 行分别对应这两种。

### A5. 另一个「一次性」的旁证（可选，10 分钟）

如果想让「只有一发」也成立，看 `WeaponDescriptor.ndf` 里武器的 `Salves`：

```
Salves = [1,]      ← 120 个武器这么写
```

`Salves` 是齐射序列。`[1]` = 只打一轮。这不是「弹药数」，WARNO 没有每单位弹药数字段  
（搜遍 `NbAmmo` / `AmmoCount` / `MaxAmmo` / `Munition` 全是 0 命中），弹药耗尽靠补给，  
所&#x4EE5;**「弹药归零」在机制上不成立，只能靠「开火后自毁」来达成一次性**。

这一点要在设计上接受：FPV 的「一次性」是技能给的，不是弹药给的。

---#验证结果#：无论什么salves长度的武器，打出第一发就立刻消失。阶段A通过。

## 4. 阶段 B：Flying 单位能不能被装载

### B1. 先理解标签体系（只有 3 个标签）

原版 389 个运输载具的 `TransportableTagSet` 只出现三种值：

| 标签                    | 承运方数量 | 装的是什么             |
| --------------------- | ----- | ----------------- |
| `Crew`                | 389   | 步兵班               |
| `Unite_transportable` | 161   | 地面车辆、牵引炮          |
| `CanBeAirlifted`      | 14    | 同上，但承运方是**运输直升机** |

被装方在自己 `TTagsModuleDescriptor.TagSet` 里带对应标签，就能被装。

**关键观察**：`CanBeAirlifted` 的 14 个承运方**全是 Flying**：

```
CH47_Chinook_US / CH47D_Chinook_SAS_UK / CH53_Sea_Stallion_US /
CH53G_trans_RFA / CH54B_Tarhe_trans_US / MH47D_Super_Chinook_US /
Mi_8T_non_arme_SOV / Mi_8TV_non_arme_SOV / Mi_8T_non_arme_DDR /
Mi_8T_non_arme_POL / Mi_8T_non_arme_TCH / Super_Puma_FR / Super_Puma_ESP /
Westland_Wessex_trans_UK          全部 seats = 1
```

也就是说：**「飞行载具装载」这件事本身是存在的**，只是它装的全是地面单位。  
D1 要验证的就是「把被装方换成 Flying 单位，还成不成立」。

### B2. 改哪里

| 文件                        | 对象                             | 改动                                                           |
| ------------------------- | ------------------------------ | ------------------------------------------------------------ |
| `GameData\Generated\Gameplay\Gfx\UniteDescriptor.ndf` | `Descriptor_Unit_Pero_UAV_SOV` | ① 加一个 `TTransportableModuleDescriptor`<br />② `TagSet` 加一个标签 |

**① 在 `ModulesDescriptors` 列表里加**（原版 1130 个内联实例，结构就是这个）：

```
TTransportableModuleDescriptor
(
    TransportedTexture = "UseInGame_Transport_Infantry"
    TransportedSoldier = 'Pero_UAV_SOV'
    NbSeatsOccupied    = 1
    TimeToLoad         = 0
    IsTowable          = False
)
```

**② 在 `TTagsModuleDescriptor.TagSet` 里加标签**。Pero 原有：

```
TagSet = [
    "Air", "AllUnits", "AllowedForMissileRoE", "Avion", "Avion_Reco",
    "Non_Radar_Targetable", "UNITE_Pero_UAV_SOV", "Unite",
]
```

**两个方案，建议都试，因为它们回答的是不同问题：**

| 方案      | 加什么标签                   | 用哪个载具装                   | 说明                                                 |
| ------- | ----------------------- | ------------------------ | -------------------------------------------------- |
| **B-1** | `"Unite_transportable"` | 任意地面运输车（BTR / 悍马 / M113） | 载具最多（161 个），最容易撞上；但飞机装进车里观感怪                       |
| **B-2** | `"CanBeAirlifted"`      | CH-47 / Mi-8T 等运输直升机     | 观感合理（直升机吊运无人机）；只有 14 个载具，`LoadRadiusGRU = 70` 要贴很近 |

**建议先做 B-2**：它是最终形态（FPV 就该从直升机上卸下来），且如果 B-2 成立，  
B-1 基本不用试 —— 反过来不成立。

### B3. 进游戏怎么验

1. 出一架 `CH47_Chinook_US`（或任意 `Mi_8T_non_arme_*`）
2. 出一架 `Pero_UAV`（改造后的那架）
3. 让直升机靠近 Pero，看**右键菜单里有没有「装载」**
4. 有就点，看 Pero 是否消失、直升机面板是否显示载员
5. 让直升机飞一段，再卸载，看 Pero 是否出现

### B4. 判定表 —— 这张表决定 D1 的生死

| 现象                   | 结论                    | 下一步              |
| -------------------- | --------------------- | ---------------- |
| 右键出现「装载」，点了能装上，能飞、能卸 | ✅✅ **D1 成立**          | 做阶段 C            |
| 右键出现「装载」，点了没反应       | ⚠️ 菜单按 tag 生成，但装载校验没过 | 见下方「半成功」         |
| 根本不出「装载」             | ❌ 引擎按 pathfind 类型过滤了  | **D1 失败**，退 D2   |
| 能装，但装上后游戏崩 / 单位卡住    | ❌ 装载后状态不兼容            | 退 D2，或改地面单位做 FPV |
| 能装，但卸载后 FPV 掉在地上不能飞  | ⚠️ 部分成立               | 可接受 —— 至少「运进场」成立 |

**「半成功」怎么处理（菜单有、点了没反应）**：

先排除三个低级原因，再判失败：

1. `NbSeatsAvailable` 够不够（这 14 个载具都是 `seats = 1`，FPV 要占 1 个，正好）
2. `LoadRadiusGRU = 70` —— 飞机场停机坪很大，Pero 和直升机可能永远进不了 70 GRU。  
   **这是最可能的原因**，不是路径类型问题。把载具的 `LoadRadiusGRU` 临时改大（如 700）再试一次
3. `NbSeatsOccupied` 是不是 1（写 0 或漏了会装不上）

**只有排除这三条之后仍然装不上，才判定 D1 失败。**

### B5. 如果 D1 失败 —— 退回 D2

| 退路     | 做法                                                     | 代价                       |
| ------ | ------------------------------------------------------ | ------------------------ |
| **D2** | 不给运输，用 `DeploymentShiftGRU` 从地图外飞进来（空降仓那套，实测 7250 GRU） | 没有「卸下」动作，观感是「从后方飞来」      |
| **D3** | 把 FPV 做成地面 `Vehicle`（可被正常运输），放弃俯冲姿态                    | 有装卸，但只能贴地平飞              |
| **D4** | 不做运输，做成「技能召唤」：在目标附近直接生成一架 FPV                          | 最自由，但要新增一个 spawn 效果，风险最高 |

**D2 的成本最低**：`DeploymentShiftGRU` 是一个数值，原版 575 个装备在用，实测空降仓用它  
（7250 GRU + 10000 km/h）。改一个数字就能达到「从远处高速冲进来」的效果。

---

## 5. 阶段 C：组装 FPV 本体（A、B 都通过后才做）

### C1. 工程结构：改 `.ndf`，不碰 `.ndfbin`

WARNO 的 mod 是**源码工程**，不是改二进制。全部在游戏根目录下：

```
D:\Steam\steamapps\common\WARNO\Mods\<你的mod>\
  GameData\Generated\Gameplay\Gfx\        ← 你改这里（.ndf 源文件）
  GameData\Generated\Gameplay\Effects\    ← 效果包也在这里
  Gen\NDF\GFX\                            ← 编译产物 .ndfbin，不要手改
  GenerateMod.bat                         ← 双击编译
  LaunchGameDevMode.bat                   ← 直接带 mod 起游戏，跳过 ModCenter
```

三条纪律：

1. **不要手建目录** —— `Mods\CreateNewMod.bat <名字>` 会从 `ModData` 模板复制一份完整数据
2. **不要改 `Mods\ModData\`** —— 官方 `ReadMe.txt` 明确说那是模板，改了其它 mod 就没有干净底本
3. **不要手改 `Gen\NDF\`** —— 每次 `GenerateMod.bat` 都会覆盖它

### C2. 先确认是哪一套单位表，再改这 5 个 `.ndf`

**WARNO 有两套完全独立的单位表，改错文件做出来的是另一个东西：**

| | 战术模式（战场） | 将军模式（AG 牌组） |
|---|---|---|
| 源文件 | `GameData\Generated\Gameplay\Gfx\UniteDescriptor.ndf` | `GameData\Generated\Gameplay\Unit\Strategic\Units.ndf` |
| 编译成 | `Gen\NDF\GFX\Unit.ndfbin` | `Gen\NDF\GFX\Pawn.ndfbin` |
| 原版单位数 | 2,898 | 2,097 |
| AG 专属 `TDeckModuleDescriptor` | **0** | **2,265** |
| 战场运输 `TTransporterModuleDescriptor` | **有（11 个载具）** | **0** |
| 「乘载具进入、择机卸下」 | ✅ 就在这里 | ❌ 没有这套机制 |

实测三个真实文件：

```
柏林 mod Pawn.ndfbin   2,268 个单位 / 2,265 个 TDeckModuleDescriptor / 0 个运输模块
光环 mod Unit.ndfbin   3,824 个单位 / 0 个 TDeckModuleDescriptor     / 11 个运输模块
Test   Weapon.ndfbin     211 个单位 / 0 个                            / 0 个
```

**所以 FPV 必须做在 `Gfx\UniteDescriptor.ndf`（战术模式）。**

> 将军模式的「运输」是牌组编辑器里选 `AvailableTransportList`（`Division.ndf`）——
> 那是「这个营配什么车」，不是「战场上从车上把兵卸下来」。两者不是一回事。
> 如果你要在 AG 里实现空投/机降，走的是 `Division.ndf` + `Capacite` 那条线，
> 不是本文的战场运输。

下面是战术模式要改的文件：

| # | 源文件（相对 mod 根目录） | 加什么 | 编译进 |
|---|---|---|---|
| 1 | `GameData\Generated\Gameplay\Gfx\UniteDescriptor.ndf` | 单位 `Descriptor_Unit_FPV_*` | `Gen\NDF\GFX\Unit.ndfbin` |
| 2 | `…\Gameplay\Gfx\Ammunition.ndf` | 弹药 `Ammo_FPV_*` | `Gen\NDF\GFX\Weapon.ndfbin` |
| 3 | `…\Gameplay\Gfx\WeaponDescriptor.ndf` | 武器 `WeaponDescriptor_FPV` | `Gen\NDF\GFX\Weapon.ndfbin` |
| 4 | `…\Gameplay\Gfx\ConditionsDescriptor.ndf` | 条件 `ConditionTimeAfterShot_*` | `Gen\NDF\GFX\EffectCapacity.ndfbin` |
| 5 | `…\Gameplay\Gfx\CapaciteList.ndf` | 技能 `Capacite_FPV_Destroy` | `Gen\NDF\GFX\EffectCapacity.ndfbin` |
| — | `…\Gameplay\Effects\EffetsSurUnite.ndf` | **不改**，只引用 | `Gen\NDF\GFX\EffectCapacity.ndfbin` |

四个必须记住的事实（都实测过）：

**① 一个 `ndfbin` 由多个 `.ndf` 合成**

```
Weapon.ndfbin         ← WeaponDescriptor.ndf + Ammunition.ndf + AmmunitionMissiles.ndf + …
EffectCapacity.ndfbin ← CapaciteList.ndf + ConditionsDescriptor.ndf + EffetsSurUnite.ndf + …
Unit.ndfbin           ← UniteDescriptor.ndf + BuildingDescriptors.ndf + …
```

所以 `~/` 这种相对引用**能跨这些 `.ndf` 文件** —— 原版 `CapaciteList.ndf` 里的技能就引用着
`EffetsSurUnite.ndf` 里定义的效果包。这也是新技能可以直接写 `~/UnitEffect_Destruction` 的原因，
**不需要把效果包搬过来**。

**② ModGen 是按需编译的**

只改 `Ammunition.ndf` 就只重新生成 `Weapon.ndfbin`。没改 `UniteDescriptor.ndf`，
`Unit.ndfbin` 根本不会出现 —— 那不是出错，是「这个逻辑文件没被改过，用原版」。

**③ 产物是完整文件，不是补丁**

`Weapon.ndfbin` 编出来含 **18,006 个对象**（原版全部武器 + 你加的）。mod 覆盖的是整个逻辑文件。

**④ `.ndf` 比 `.ndfbin` 省事的地方**

| | `.ndf` 工程 | 直接改 `.ndfbin` |
|---|---|---|
| 新增对象 | 写一段文本，编译器建索引 | 要克隆、要处理二进制线格式 |
| GUID | 直接写 `GUID:{...}` 文本 | 要写 16 字节 |
| 路径引用 | 编译器按 `ClassNameForDebug` 自动解析 | 需自己保证能被 `trans_ref` 找到 |
| 改错排查 | 编译报文件名和行号 | 只能进游戏看现象 |

**所以只要有工程，就别走改 ndfbin 那条路。** 后者的唯一用途是没有源码的别人的 mod。

### C3. 你的工程里现在有什么（实测 `Mods\Test`）

| 对象 | 位置 | 状态 |
|---|---|---|
| `Ammo_RocketFPV_RPG7VL` | `Ammunition.ndf` 第 66677 行 | ✅ 已建（原版无此对象） |
| `ConditionTimeAfterShot_1000` | `ConditionsDescriptor.ndf` 第 158 行 | ✅ 已建，`Duration = 1` |
| `WeaponDescriptor_FPV` | `WeaponDescriptor.ndf` | ❌ 还没有 |
| `Capacite_FPV_*` | `CapaciteList.ndf` | ❌ 还没有 |
| FPV 单位 | `UniteDescriptor.ndf` | ❌ 还没有（`Gen\NDF\GFX\Unit.ndfbin` 不存在，说明这个文件没被改过） |

你那个弹药我核了一遍，**关键参数都对**：

```
MaximumRangeGRU      = 50      ← 比原版最小先例（150）还短，自己突破了
CanShootWhileMoving  = True    ← 俯冲时能开火，这条最容易漏
PiercingWeapon       = True    ← 能穿甲打坦克
PhysicalDamages      = 1.0
TimeBetweenTwoSalvos = 6.0     ← 配合「开火后自毁」，这个冷却其实用不上（单位已经没了）
```

唯一要留意的是 `SupplyCost = 15.0` —— 这是抄 RPG7VL 带来的。单发弹药这个值没有实际影响，
但如果你之后要做「补给一次给几发」，得重新算。

### C4. 三个待建对象怎么写

（弹药和条件你已经有了；下面三个是按顺序要补的。若换到新工程从零开始，
弹药和条件照 5-C2 的表格加进去，写法同下。）

#### ① 武器：追加到 `WeaponDescriptor.ndf` 末尾

```ndf
export WeaponDescriptor_FPV is TWeaponManagerModuleDescriptor
(
    AlwaysOrientArmorTowardsThreat = False
    Salves = [1,]
    TurretDescriptorList = [
        TTurretTwoAxisDescriptor
        (
            AngleRotationBase = 0
            AngleRotationMax = 6.283185307179586
            AngleRotationMaxPitch = 1.3089969389957472
            AngleRotationMinPitch = -0.2617993877991494
            MountedWeaponDescriptorList = [
                TMountedWeaponDescriptor
                (
                    AmmoBoxIndex = 0
                    Ammunition = $/GFX/Weapon/Ammo_RocketFPV_RPG7VL   // ← 指向你已有的弹药
                    AnimateOnlyOneSoldier = False
                    EffectTag = "FireEffect_SAM_9M8M3_x2"            // 抄现成的，别造新标签
                    NbWeapons = 1
                    WeaponActiveAndCanShootPropertyName = "WeaponActiveAndCanShoot_1"
                    WeaponIgnoredPropertyName = "WeaponIgnored_1"
                    WeaponShootDataPropertyName = ["WeaponShootData_0_1"]
                ),
            ]
            OutOfRangeTrackingDuration = 3.5
            Tag = "tourelle1"
            VitesseRotation = 2.0943951023931953
            YulBoneOrdinal = 1
        ),
    ]
)
```

#### ② 技能：追加到 `CapaciteList.ndf` 末尾

`CapaciteList.ndf` 只有 1,396 行，照抄炮兵那个技能改三处最省事：

```ndf
export Capacite_FPV_Destroy is TCapaciteDescriptor
(
    DescriptorId            = GUID:{换成没被用过的}
    Name                    = "FPV_Destroy"
    StackPolicy             = ~/CapacityStackPolicy_never
    Trigger                 = ~/CapacityTriggerType_automatic
    TargetTeamFilter        = ~/CapaciteTargetFilter_joueur
    InfluenceMapAlliance    = ~/AllianceRelation/vide
    RangeGRU                = 0
    CastTime                = 0.00
    CheckVisibility         = True
    CanBeCastFromTransport  = False
    TargetEffect            = ~/UnitEffect_Destruction      // ← 唯一的实质改动
    CapacityDuration        = -1.00
    TargetInBuilding        = False
    TargetInTransport       = False
    TargetInSelf            = False
    TargetMySelf            = True
    FeedbackActivationMask  = ~/CapaciteFeedbackActivationMask_never
    DisplayRangeColor       = RGBA[0,0,0,0]
    DisplayRangeThickness   = 0.00
    AllowedTargetTags       = [
    ]
    ForbiddenTargetTags     = [
    ]
    Conditions              = [
        ~/ConditionTimeAfterShot_1000,     // ← 指向你已建的那个
    ]
)
```

#### ③ 单位：追加到 `UniteDescriptor.ndf` 末尾

`UniteDescriptor.ndf` 有 71 万行，**只用追加，不要在中间插**。复制原版 Pero 那一整块
（`export Descriptor_Unit_Pero_UAV_SOV is TEntityDescriptor` 到它结尾的 `)`），改 6 处：

| 改什么 | 原值 | 改成 |
|---|---|---|
| 名字 | `Descriptor_Unit_Pero_UAV_SOV` / `ClassNameForDebug = 'Unit_Pero_UAV_SOV'` | `Descriptor_Unit_FPV_Test` / `'Unit_FPV_Test'` |
| `DescriptorId` | 原 GUID | 换成没被用过的 |
| 模块列表里加一行 | — | `$/GFX/Weapon/WeaponDescriptor_FPV` |
| `TCapaciteModuleDescriptor.DefaultSkillList` | `[$/GFX/EffectCapacity/Capacite_UAV]` | `[$/GFX/EffectCapacity/Capacite_FPV_Destroy]` |
| `TagSet` | 8 个标签 | 加 `"Unite_transportable"` 或 `"CanBeAirlifted"`（阶段 B 的结论） |
| 模块列表里加 | — | `TTransportableModuleDescriptor`（见 4-B2） |

`TTacticalLabelModuleDescriptor` 里的 `MimeticName` / `ReferenceMesh` / `BlackHoleKey`
可以先照抄 Pero 的（临时用它的模型），等美术就位再换。

> **GUID 怎么取**：随便生成一个 UUID 再包成 `GUID:{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}`。
> 关键是**别和原版撞**。全库搜一遍你用的值，0 命中就是安全的。

### C5. 编译与验证

**你的编译链路已经通过一次了** —— `Gen\NDF\GFX\` 下 `EffectCapacity.ndfbin`（11:08）和
`Weapon.ndfbin`（11:09）都是今天生成的，说明 `GenerateMod.bat` 本身没问题，
问题只会在 `.ndf` 内容上。

```
1. 在 mod 目录里双击 GenerateMod.bat
2. 看 Gen\NDF\GFX\ 下对应 .ndfbin 的时间戳有没有变
   只改了 Ammunition.ndf 的话，只有 Weapon.ndfbin 会更新
3. 双击 LaunchGameDevMode.bat
   它等价于 WARNO.exe -activatemods "<mod名>" -devmode -rootdata "Mods/<mod名>"
   —— 直接带着这个 mod 起游戏，不用进 ModCenter 激活
4. 验
```

**编译失败**时控制台会打印出错的文件和行号，最常见三种：少逗号、GUID 重复、
引用了不存在的 `$/GFX/...` 路径。

### C6. 俯冲：改一个数就行，但要实测

单位模块里的 `AirplaneMovementDescriptor`：

```ndf
AirplaneMovementDescriptor
(
    AltitudeGRU    = 525     // 巡航高度
    AltitudeMinGRU = 100     // ← 最低高度，想俯冲就往下压
    SpeedInKmph    = 500
    ...
)
```

Pero 是 `AltitudeMinGRU = 100`。压到 10~30 试试。

> **未验证**：压低后会不会被引擎按「飞机不能贴地」的规则拉回去。
> 如果拉不回来，观感变成「低空平飞开火」，功能不减。

---

## 6. 字段速查表

### 源文件与编译产物的对应（实测）

| 源文件（`Mods\<mod>\GameData\Generated\Gameplay\`） | 编译进 | 里面装什么（原版实测） |
|---|---|---|
| `Gfx\UniteDescriptor.ndf` | `Gen\NDF\GFX\Unit.ndfbin` | **战术单位 2,898** ← FPV 放这里 |
| `Gfx\BuildingDescriptors.ndf` | 同上 | 建筑 13（FOB 之类） |
| `Unit\Strategic\Units.ndf` | `Gen\NDF\GFX\Pawn.ndfbin` | 将军模式营 2,097（**不是 FPV 用的**） |
| `Gfx\Ammunition.ndf` | `Gen\NDF\GFX\Weapon.ndfbin` | 弹药 952 |
| `Gfx\AmmunitionMissiles.ndf` | 同上 | 导弹弹药 197 |
| `Gfx\WeaponDescriptor.ndf` | 同上 | 武器描述符 2,506 |
| `Gfx\CapaciteList.ndf` | `Gen\NDF\GFX\EffectCapacity.ndfbin` | 技能 48 |
| `Gfx\ConditionsDescriptor.ndf` | 同上 | 技能触发条件 |
| `Effects\EffetsSurUnite.ndf` | 同上 | 效果包 202 + `RemoveUnit` |

### 关键字段与先例

| 要做什么 | 字段 | 在哪个 `.ndf` | 原版先例 |
|---|---|---|---|
| 让单位可被装 | `TTransportableModuleDescriptor` | `UniteDescriptor.ndf` | 1130 个内联实例 |
| 指定谁能装它 | `TagSet` 加 `Unite_transportable` / `CanBeAirlifted` | `UniteDescriptor.ndf` | 161 / 14 个承运方 |
| 射程压短 | `MaximumRangeGRU` | `Ammunition.ndf` | 原版最小 150（你的 50 是自建） |
| 边飞边打 | `CanShootWhileMoving` | `Ammunition.ndf` | 需 `True` |
| 只打一轮 | `Salves = [1,]` | `WeaponDescriptor.ndf` | 120 个 |
| 开火后触发 | `TConditionTimeAfterShot.Duration` | `ConditionsDescriptor.ndf` | 炮兵版 `Duration = 15` |
| 自动触发 | `Trigger = ~/CapacityTriggerType_automatic` | `CapaciteList.ndf` | 炮兵技能 |
| 作用于自己 | `TargetMySelf = True` | `CapaciteList.ndf` | 炮兵技能 |
| 让单位消失 | `TargetEffect = ~/UnitEffect_Destruction` | `CapaciteList.ndf` | 原版有包、**无使用者** |
| 从地图外飞入 | `DeploymentShiftGRU` | `UniteDescriptor.ndf` | 空降仓 7250、侦察兵 2500 |
| 死亡爆炸 | `DeathExplosionAmmo` | `UniteDescriptor.ndf`（`UnitCadaverGeneratorModuleDescriptor`） | Pero 用它 |

### 原版无人机可参考的 10 个同类

挂 `Capacite_UAV` 的全是 Flying 侦察机，可以拿来对比参数：

```
Pero_UAV_SOV / Pchela_1T_SOV / Tu_143_SOV / Tu_143_TCH /
MQM_105_Aquila_US / YCQM_121A_Pave_Tiger_US / CL_89_CAN / CL_89_FR /
CL_289_RFA / Epervier_BEL
```

---

## 7. mod 工程怎么建

**用官方脚本，不要手搓。**

```
1. 在 WARNO\Mods\ 下运行：CreateNewMod.bat <你的Mod名>
   → 从 ModData 模板复制一份完整数据（GameData / CommonData / Gen / 全套 .bat）
2. 改 GameData\Generated\ 下的 .ndf
3. 双击 mod 目录里的 GenerateMod.bat
4. 双击 LaunchGameDevMode.bat 直接验
5. 发布时 UploadMod.bat
```

工程目录里自带的脚本：

| 脚本 | 作用 |
|---|---|
| `GenerateMod.bat` | 编译 `.ndf` → `Gen\NDF\*.ndfbin` |
| `LaunchGameDevMode.bat` | 以 dev 模式启动游戏并激活本 mod（`-activatemods` + `-devmode` + `-rootdata`） |
| `UpdateMod.bat` | 游戏版本更新后，把工程数据升到新版 |
| `UploadMod.bat` | 上传 Workshop |
| `CreateModBackup.bat` / `RetrieveModBackup.bat` | 备份 / 还原 |

**`Config.ini` 不用手填。** `CreateNewMod` 会生成，`UpdateMod` 会在版本更新时重算
`[Config]` 段的哈希。手填只在一种情况下需要：你拿到的是别人的 ndfbin、想手工打包成 mod
—— 那是另一条路，不在本 ToDo 范围内。

---

## 8. 风险登记

| # | 风险 | 影响 | 什么时候会发现 | 缓解 |
|---|---|---|---|---|
| R1 | **Flying 单位装不进载具** | D1 直接失败 | 阶段 B | 退 D2（`DeploymentShiftGRU` 飞入） |
| R2 | `LoadRadiusGRU = 70` 太小，装不上 | 误判 R1 成立 | 阶段 B 第 3 步 | 临时改大到 700 再试 |
| R3 | 新单位的 `ClassNameForDebug` 与路径不一致 | 引用解析失败 | 阶段 C 编译或首次进游戏 | 路径末段必须等于 `"Descriptor_" + ClassNameForDebug` |
| R4 | 五个 `.ndf` 里任意一个改坏，编译直接失败 | 整个 mod 不生成 | 编译时 | 控制台给文件名+行号；一次只改一个文件 |
| R5 | `ConditionTimeAfterShot` 语义与预期相反 | 手感不对但不致命 | 阶段 A | 调 `Duration`，或换触发方式 |
| R6 | GUID 与原版重复 | 对象被覆盖 / 加载异常 | 编译或运行时 | 新对象用新 UUID，全库搜 0 命中 |
| R7 | `AltitudeMinGRU` 压不下去 | 俯冲变平飞 | 阶段 C 调参 | 接受为「低空平飞」，功能不减 |
| R8 | 改了 `Gen\NDF\` 里的产物 | 下次编译被覆盖，白改 | 下次 `GenerateMod` | 只改 `GameData\`，永不碰 `Gen\` |

**R2 特别标注**：这 14 个运输直升机的 `NbSeatsAvailable = 1`、`LoadRadiusGRU = 70`。
70 GRU 大约是一个车身长。Pero 停在机场停机坪，直升机要落到几乎重叠才够得着。
**如果阶段 B 不出现「装载」菜单，先怀疑这个，再怀疑路径类型。**

**R3 说明**：`.ndf` 路线下这条比改 ndfbin 轻得多 —— 编译器会按 `export` 的名字建索引，
只要 `export Descriptor_Unit_FPV_Test` 和 `ClassNameForDebug = 'Unit_FPV_Test'` 对得上，
路径 `$/GFX/Unit/Descriptor_Unit_FPV_Test` 就能解析到。原版 2898 个单位全都遵循这个规则。


## 9. 一句话判定

```
阶段 A 通过  → 「开火后自毁」这条链成立，FPV 的「一次性」有了
阶段 B 通过  → 「飞机能被运」这条成立，D1 成立，FPV 的出场有了
两者都过    → 阶段 C 只是组装，剩下的全是调参和美术
阶段 B 失败  → 退 D2：不做运输，用 DeploymentShiftGRU 从地图外冲进来
```

**别跳过 B 直接做 C。** C 要横跨五个 `.ndf` 新增五个对象，如果 B 不成立，
C 做出来的东西只能当固定炮台用 —— 那是另一件事了。
