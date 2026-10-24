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

| | 状态 |
|---|---|
| 空中单位能不能挂武器 | ✅ 已证：`Flying` 单位里 **510 个**带武器描述符 |
| 射程能不能压到极短 | ✅ 已证：947 个弹药里**最小 150 GRU**，`MinimumRangeGRU` 有 198 个用 0 |
| 开火后能不能自动做一件事 | ✅ 已证：`Capacite_Artillerie_tag_on_shoot` 是 `automatic` 触发器 + 开火后条件 |
| 能不能让单位自己消失 | ✅ 已证：`UnitEffect_Destruction` = 一条 `TUnitEffectRemoveUnitDescriptor` |
| **Flying 单位能不能被运输装载** | ❌ **唯一未知**：637 个 Flying 单位里 **0 个**带运输模块，原版零先例 |

所以 D1 的全部工作量，就是回答最后这一行。**其余都是已知路径，不需要探索。**

### 为什么这个问题必须实测

`TTransportableModuleDescriptor` 只有 5 个字段（`TransportedTexture` / `TransportedSoldier` /
`NbSeatsOccupied` / `TimeToLoad` / `IsTowable`），**没有任何「只限地面」的开关**。
装载资格是靠载具的 `TransportableTagSet` 做**标签字符串匹配**的。

也就是说：数据层面没有任何东西阻止一架飞机被装进车里。引擎层面有没有别处的硬校验
（比如装载时检查 pathfind 类型），**数据回答不了**。这就是必须进游戏的原因。

---

## 2. 验证总览：三个阶段，别一次全做

| 阶段 | 验证什么 | 改几个文件 | 改几处 | 预计 | 依赖 |
|---|---|---|---|---|---|
| **A** | 开火 → 自己消失 | 2 | 2 个值 | 半天 | 无 |
| **B** | Flying 单位能不能被装载 | 1 | 2 处 | 半天 | 无 |
| **C** | 组装成 FPV 本体 | 3 | 新增 4 个对象 | 1~2 天 | A + B 都通过 |

**A 和 B 互不依赖，可以并行做，也可以只做一个先看结果。**

阶段 A 用**自行火炮**验证，与飞机无关 —— 因为「技能 → 开火后触发 → RemoveUnit」这条链
跟移动类型没关系，用炮验最快、最省事、噪音最小。

阶段 B 只改 Pero 一个单位，不加任何新对象。

只有 A、B 都过了，才值得做 C（C 要新增对象、跨三个文件，成本高得多）。

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

| 文件 | 对象 | 字段 | 原值 | 改成 |
|---|---|---|---|---|
| `Gen/NDF/GFX/EffectCapacity.ndfbin` | `Capacite_Artillerie_tag_on_shoot` | `TargetEffect` | `~/UnitEffect_Ajoute_Tag_CBR_cible` | `~/UnitEffect_Destruction` |

就这一处。

`UnitEffect_Destruction` 在同一个文件里（实测 `EffectCapacity.ndfbin` 有 1 个
`TUnitEffectRemoveUnitDescriptor`、226 个 `TEffectsPackDescriptor`），内容是：

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

| 现象 | 结论 | 下一步 |
|---|---|---|
| 开炮后炮立刻消失 | ✅ **链路通**，`Duration` 可能是 0 或立即生效 | 进阶段 C |
| 开炮后约 15 秒消失 | ✅ **链路通**，`Duration` = 延迟秒数 | 进阶段 C，按需调 `Duration` |
| 炮消失但残骸/爆炸都没有 | ✅ 机制通，只是观感缺失 | 后面挂 `DeathExplosionAmmo` 补爆炸 |
| 开炮后什么都没发生 | ❌ 技能没触发 | 见下方排查 |
| 炮没消失，反而所有同型号炮都变强/变弱 | ❌ 你改错了对象（改到了效果包内容） | 回滚，只改 `TargetEffect` 这一行 |

**如果什么都没发生**，先排查这三点（按可能性排序）：

1. `Trigger` 还是不是 `~/CapacityTriggerType_automatic`
2. `TargetMySelf` 还是不是 `True`（改成 `False` 就变成对目标生效，炮不会消失）
3. `Conditions` 里 `ConditionTimeAfterShot_15000` 还在不在

> **未验证的语义**：`TConditionTimeAfterShot` 只有 `Duration` 一个参数（炮兵版是 15）。
> 它到底是「开火后 N 秒内持续生效」还是「开火后等 N 秒」，数据看不出来 ——
> 但**两种语义对 FPV 都能用**：前者开火即爆，后者延迟爆。判定表的第 1、2 行分别对应这两种。

### A5. 另一个「一次性」的旁证（可选，10 分钟）

如果想让「只有一发」也成立，看 `Weapon.ndfbin` 里武器的 `Salves`：

```
Salves = [1,]      ← 120 个武器这么写
```

`Salves` 是齐射序列。`[1]` = 只打一轮。这不是「弹药数」，WARNO 没有每单位弹药数字段
（搜遍 `NbAmmo` / `AmmoCount` / `MaxAmmo` / `Munition` 全是 0 命中），弹药耗尽靠补给，
所以**「弹药归零」在机制上不成立，只能靠「开火后自毁」来达成一次性**。

这一点要在设计上接受：FPV 的「一次性」是技能给的，不是弹药给的。

---

## 4. 阶段 B：Flying 单位能不能被装载

### B1. 先理解标签体系（只有 3 个标签）

原版 389 个运输载具的 `TransportableTagSet` 只出现三种值：

| 标签 | 承运方数量 | 装的是什么 |
|---|---|---|
| `Crew` | 389 | 步兵班 |
| `Unite_transportable` | 161 | 地面车辆、牵引炮 |
| `CanBeAirlifted` | 14 | 同上，但承运方是**运输直升机** |

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

| 文件 | 对象 | 改动 |
|---|---|---|
| `Gen/NDF/GFX/Unit.ndfbin` | `Descriptor_Unit_Pero_UAV_SOV` | ① 加一个 `TTransportableModuleDescriptor`<br>② `TagSet` 加一个标签 |

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

| 方案 | 加什么标签 | 用哪个载具装 | 说明 |
|---|---|---|---|
| **B-1** | `"Unite_transportable"` | 任意地面运输车（BTR / 悍马 / M113） | 载具最多（161 个），最容易撞上；但飞机装进车里观感怪 |
| **B-2** | `"CanBeAirlifted"` | CH-47 / Mi-8T 等运输直升机 | 观感合理（直升机吊运无人机）；只有 14 个载具，`LoadRadiusGRU = 70` 要贴很近 |

**建议先做 B-2**：它是最终形态（FPV 就该从直升机上卸下来），且如果 B-2 成立，
B-1 基本不用试 —— 反过来不成立。

### B3. 进游戏怎么验

1. 出一架 `CH47_Chinook_US`（或任意 `Mi_8T_non_arme_*`）
2. 出一架 `Pero_UAV`（改造后的那架）
3. 让直升机靠近 Pero，看**右键菜单里有没有「装载」**
4. 有就点，看 Pero 是否消失、直升机面板是否显示载员
5. 让直升机飞一段，再卸载，看 Pero 是否出现

### B4. 判定表 —— 这张表决定 D1 的生死

| 现象 | 结论 | 下一步 |
|---|---|---|
| 右键出现「装载」，点了能装上，能飞、能卸 | ✅✅ **D1 成立** | 做阶段 C |
| 右键出现「装载」，点了没反应 | ⚠️ 菜单按 tag 生成，但装载校验没过 | 见下方「半成功」 |
| 根本不出「装载」 | ❌ 引擎按 pathfind 类型过滤了 | **D1 失败**，退 D2 |
| 能装，但装上后游戏崩 / 单位卡住 | ❌ 装载后状态不兼容 | 退 D2，或改地面单位做 FPV |
| 能装，但卸载后 FPV 掉在地上不能飞 | ⚠️ 部分成立 | 可接受 —— 至少「运进场」成立 |

**「半成功」怎么处理（菜单有、点了没反应）**：

先排除三个低级原因，再判失败：

1. `NbSeatsAvailable` 够不够（这 14 个载具都是 `seats = 1`，FPV 要占 1 个，正好）
2. `LoadRadiusGRU = 70` —— 飞机场停机坪很大，Pero 和直升机可能永远进不了 70 GRU。
   **这是最可能的原因**，不是路径类型问题。把载具的 `LoadRadiusGRU` 临时改大（如 700）再试一次
3. `NbSeatsOccupied` 是不是 1（写 0 或漏了会装不上）

**只有排除这三条之后仍然装不上，才判定 D1 失败。**

### B5. 如果 D1 失败 —— 退回 D2

| 退路 | 做法 | 代价 |
|---|---|---|
| **D2** | 不给运输，用 `DeploymentShiftGRU` 从地图外飞进来（空降仓那套，实测 7250 GRU） | 没有「卸下」动作，观感是「从后方飞来」 |
| **D3** | 把 FPV 做成地面 `Vehicle`（可被正常运输），放弃俯冲姿态 | 有装卸，但只能贴地平飞 |
| **D4** | 不做运输，做成「技能召唤」：在目标附近直接生成一架 FPV | 最自由，但要新增一个 spawn 效果，风险最高 |

**D2 的成本最低**：`DeploymentShiftGRU` 是一个数值，原版 575 个装备在用，实测空降仓用它
（7250 GRU + 10000 km/h）。改一个数字就能达到「从远处高速冲进来」的效果。

---

## 5. 阶段 C：组装 FPV 本体（A、B 都通过后才做）

### C1. 文件与对象清单

| # | 文件 | 新增对象 | 内容 |
|---|---|---|---|
| 1 | `Gen/NDF/GFX/Unit.ndfbin` | `Descriptor_Unit_FPV_XXX` | 复制 Pero，改 4 处 |
| 2 | `Gen/NDF/GFX/Weapon.ndfbin` | `Ammo_FPV_Kamikaze` | 复制 `Ammo_Mort_Avion_Leger` |
| 3 | `Gen/NDF/GFX/Weapon.ndfbin` | `WeaponDescriptor_FPV` | 复制直升机武器，指向新弹药 |
| 4 | `Gen/NDF/GFX/EffectCapacity.ndfbin` | `ConditionTimeAfterShot_1000` | 复制炮兵版，`Duration` 改 1 |
| 5 | `Gen/NDF/GFX/EffectCapacity.ndfbin` | `Capacite_FPV_Destroy` | 复制炮兵技能，`TargetEffect` → `~/UnitEffect_Destruction` |

`UnitEffect_Destruction` **不新增**，直接引用已有实例。

**新增对象必须满足两条**（这是整个阶段最大的工程风险）：

1. **GUID 唯一**：每个新对象要有自己的 `DescriptorId = GUID:{...}`。抄原对象时必须换掉。
2. **能被 `trans_ref` 路径找到**：别的对象用 `$/GFX/Unit/Descriptor_Unit_FPV_XXX` 这种
   **字符串路径**引用它。原版实测的对应规则是：

   ```
   路径末段 = "Descriptor_" + ClassNameForDebug
   $/GFX/Unit/Descriptor_Unit_FPV_X  ←→  ClassNameForDebug = 'Unit_FPV_X'
   ```

   > **未验证**：新建对象设好 `ClassNameForDebug` 后，游戏能不能按路径解析到它。
   > 克隆 + 改名的对象在 ndfbin 里能正常落盘、能重新读出（已验证），
   > 但「游戏运行时按字符串路径找到新对象」这一步没有先例可查。
   >
   > **建议先做一个 10 分钟的探针**：在 `Weapon.ndfbin` 里克隆一个弹药、改名、
   > 然后把某个单位的武器指向它。生效 = 新增对象可被解析，阶段 C 才值得投入。

### C2. NDF 草案（可直接抄，注意换 GUID）

**① 弹药**

```ndf
export Ammo_FPV_Kamikaze is TAmmunitionDescriptor
(
    DescriptorId            = GUID:{换成新 GUID}
    Name                    = 'None'
    TypeCategoryName        = 'None'
    Caliber                 = ''
    TraitsToken             = [ ]
    WeaponCursorType        = Weapon_Cursor_None
    Arme                    = TDamageTypeRTTI(Family=DamageFamily_he Index=1)
    ProjectileType          = EProjectileType/Fake
    ImpactHappening         = 'DestructionUniteAvion'
    FxPower                 = EFXPower/Moyen
    TimeBetweenTwoShots     = 0.5
    TimeBetweenTwoFx        = 0.5
    MinimumRangeGRU         = 0
    MaximumRangeGRU         = 150      // 原版最小先例；想更近就 50，但无先例
    MinimumRangeHelicopterGRU = 0
    MaximumRangeHelicopterGRU = 0
    MinimumRangeAirplaneGRU = 0
    MaximumRangeAirplaneGRU = 0
    MinimumRangeProjectileGRU = 0
    MaximumRangeProjectileGRU = 0
    AngleDispersion         = 0.0
    SalvoShotsSorted        = True
    RadiusSplashPhysicalDamagesGRU = 0
    PhysicalDamages         = 1.0     // ← 毁伤不够就调这里（原版自杀弹药是 1.0）
    ShowDamageInUI          = False
    RadiusSplashSuppressDamagesGRU = 0
    SuppressDamages         = 0.0
    AllowSuppressDamageWhenNoImpact = True
    DisplaySalveAccuracy    = True
    TirIndirect             = False
    TirReflexe              = True
    ProjectileSpeedGRU      = 0.0
    ShotsBeforeMaxNoise     = 1
    TargetOnlyOneUnitInDistrict = True
    TimeBetweenTwoSalvos    = 0.5
    TimeBetweenTwoSalvos_Min = -1.0
    TimeBetweenTwoSalvos_Max = -1.0
    ShotsCountPerSalvo      = 1
    SupplyCost              = 1.0
    CanShootOnPosition      = False
    CanShootWhileMoving     = True     // ← FPV 要边俯冲边能开火，这条很重要
    HasDeploymentTime       = False
    SimultaneousShotsCount  = 1
    AffichageMunitionParSalve = 1
    SmokeDescriptor         = nil
    FireDescriptor          = nil
    IsHarmlessForAllies     = False
    PiercingWeapon          = False    // 打坦克改成 True
    TandemCharge            = False
    DamageTypeEvolutionOverRangeDescriptor = nil
)
```

> `CanShootWhileMoving` 原版自杀弹药是 `False`。FPV 是俯冲攻击，**必须改成 `True`**，
> 否则飞着的时候开不了火。这是最容易漏的一条。

**② 武器**

```ndf
export WeaponDescriptor_FPV is TWeaponManagerModuleDescriptor
(
    AlwaysOrientArmorTowardsThreat = False
    Salves = [1,]                      // 只打一轮
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
                    Ammunition = $/GFX/Weapon/Ammo_FPV_Kamikaze
                    AnimateOnlyOneSoldier = False
                    EffectTag = "FireEffect_SAM_9M8M3_x2"   // 抄现成的，别造新标签
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

**③ 条件**

```ndf
ConditionTimeAfterShot_1000 is TConditionTimeAfterShot
(
    DescriptorId = GUID:{换成新 GUID}
    Duration = 1
)
```

**④ 技能**

```ndf
export Capacite_FPV_Destroy is TCapaciteDescriptor
(
    DescriptorId            = GUID:{换成新 GUID}
    Name                    = "FPV_Destroy"
    StackPolicy             = ~/CapacityStackPolicy_never
    Trigger                 = ~/CapacityTriggerType_automatic
    TargetTeamFilter        = ~/CapaciteTargetFilter_joueur
    InfluenceMapAlliance    = ~/AllianceRelation/vide
    RangeGRU                = 0
    CastTime                = 0.00
    CheckVisibility         = True
    CanBeCastFromTransport  = False
    TargetEffect            = ~/UnitEffect_Destruction
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
        ~/ConditionTimeAfterShot_1000,
    ]
)
```

**⑤ 单位：在 Pero 基础上改这 4 处**

| 位置 | 原值 | 改成 |
|---|---|---|
| `ModulesDescriptors` 里加一行 | — | `$/GFX/Weapon/WeaponDescriptor_FPV` |
| `TCapaciteModuleDescriptor.DefaultSkillList` | `[$/GFX/EffectCapacity/Capacite_UAV]` | `[$/GFX/EffectCapacity/Capacite_FPV_Destroy]` |
| `TTagsModuleDescriptor.TagSet` | 8 个标签 | 加 `"Unite_transportable"` 或 `"CanBeAirlifted"` |
| 加 `TTransportableModuleDescriptor` | — | 见 4-B2 |

其余照抄 Pero：

```
TGenericMovementModuleDescriptor(MaxSpeedInKmph = 500, PathfindType = $/Pathfind/PathfindTypes/Flying)
AirplaneMovementDescriptor(AltitudeGRU = 525, AltitudeMinGRU = 100, SpeedInKmph = 500, ...)
TBaseDamageModuleDescriptor(MaxPhysicalDamages = 2, ...)
TProductionModuleDescriptor(FactoryType = Factory/Recons, ...)
TUnitUIModuleDescriptor(PriceCategory = 'tank_D', UnitRole = 'uav', ...)
```

### C3. 俯冲：改一个数就行，但要实测

```
AirplaneMovementDescriptor
(
    AltitudeGRU    = 525     // 巡航高度
    AltitudeMinGRU = 100     // ← 最低高度，想俯冲就往下压
    ...
)
```

Pero 是 `AltitudeMinGRU = 100`。想做出俯冲观感，把它压到 10~30。

> **未验证**：压低后会不会被引擎按「飞机不能贴地」的规则拉回去。
> 如果拉不回来，观感变成「低空平飞开火」，仍然可用。

---

## 6. 字段速查表

### 文件对应关系（实测，不是猜）

| 逻辑路径 | 实际文件 | 里面装什么 |
|---|---|---|
| `GFX/Unit` | `Gen/NDF/GFX/Unit.ndfbin` | 单位实体 + 所有模块（含运输模块、tag） |
| `GFX/Weapon` | `Gen/NDF/GFX/Weapon.ndfbin` | 武器描述符（2622 个）+ 弹药（1304 个） |
| `GFX/EffectCapacity` | `Gen/NDF/GFX/EffectCapacity.ndfbin` | 技能 72 + 效果包 226 + 条件 + `RemoveUnit` 1 |

### 关键字段与先例

| 要做什么 | 字段 | 位置 | 原版先例 |
|---|---|---|---|
| 让单位可被装 | `TTransportableModuleDescriptor` | 单位模块列表 | 1130 个内联实例 |
| 指定谁能装它 | `TagSet` 加 `Unite_transportable` / `CanBeAirlifted` | `TTagsModuleDescriptor` | 161 / 14 个承运方 |
| 射程压短 | `MaximumRangeGRU` | 弹药 | 最小 150（5 个弹药） |
| 边飞边打 | `CanShootWhileMoving` | 弹药 | 需改 `True` |
| 只打一轮 | `Salves = [1,]` | 武器描述符 | 120 个 |
| 开火后触发 | `TConditionTimeAfterShot.Duration` | 条件 | 炮兵版 `Duration = 15` |
| 自动触发 | `Trigger = ~/CapacityTriggerType_automatic` | 技能 | 炮兵技能 |
| 作用于自己 | `TargetMySelf = True` | 技能 | 炮兵技能 |
| 让单位消失 | `TargetEffect = ~/UnitEffect_Destruction` | 技能 | 原版有包、**无使用者** |
| 从地图外飞入 | `DeploymentShiftGRU` | 模块 | 空降仓 7250、侦察兵 2500 |
| 死亡爆炸 | `DeathExplosionAmmo` | `UnitCadaverGeneratorModuleDescriptor` | Pero 用它 |

### 原版无人机可参考的 10 个同类

挂 `Capacite_UAV` 的全是 Flying 侦察机，可以拿来对比参数：

```
Pero_UAV_SOV / Pchela_1T_SOV / Tu_143_SOV / Tu_143_TCH /
MQM_105_Aquila_US / YCQM_121A_Pave_Tiger_US / CL_89_CAN / CL_89_FR /
CL_289_RFA / Epervier_BEL
```

---

## 7. mod 骨架（如果还没有 mod）

最小 mod 只需要三样：

```
MyFPVMod/
  Config.ini
  Gen/NDF/GFX/Unit.ndfbin            ← 阶段 B 只改这个
  Gen/NDF/GFX/Weapon.ndfbin          ← 阶段 C 加这个
  Gen/NDF/GFX/EffectCapacity.ndfbin  ← 阶段 C 加这个
```

`Config.ini`：

```ini
[Properties]
Name = FPV Test
TagList = Gameplay
PreviewImagePath =
ID = 1234567890            ; 本地测试随便填，发布时用 Steam Workshop ID
Version = 0
DeckFormatVersion = 1
ModGenVersion = 201602
CosmeticOnly = 0

[Config] ; Used for mod compatibility check, do not modify
GFX/Unit=98cb835f3ddcd279dffea9ee18e119a0
GFX/Weapon=a295ea541c9d5a6e3fe9acad7615bd85
GFX/EffectCapacity=1ba8b23a0594dec9eb52673efb5d7a12
```

**`[Config]` 段怎么填**（这一段最容易搞错）：

- 它的格式是 `逻辑路径=哈希`，**不是文件路径列表**
- 哈希的含义是「基于哪个原版版本改的」，不是内容校验 —— 证据：柏林 mod 与光环 mod
  两份完全不同的 `Deck.ndfbin`，`GFX/Deck` 的哈希**一模一样**
- **只列你实际覆盖的文件**。阶段 B 只写 `GFX/Unit` 一行；阶段 C 才加另外两行
- 上面三个哈希取自一个覆盖了这三个文件的实际 mod，可以直接用
- ⚠️ **游戏版本更新后这些哈希会变**。如果进游戏提示 mod 不兼容，从同版本的其它 mod
  里抄这三行，或者用官方 mod 工具重新生成

---

## 8. 风险登记

| # | 风险 | 影响 | 什么时候会发现 | 缓解 |
|---|---|---|---|---|
| R1 | **Flying 单位装不进载具** | D1 直接失败 | 阶段 B | 退 D2（`DeploymentShift` 飞入） |
| R2 | `LoadRadiusGRU = 70` 太小，装不上 | 误判 R1 成立 | 阶段 B 第 3 步 | 临时改大到 700 再试 |
| R3 | 新增对象不能被 `trans_ref` 路径解析 | 阶段 C 全废 | 阶段 C 第一个对象 | 先做 10 分钟探针（5-C1 末尾） |
| R4 | 跨 3 个文件编辑，任一文件坏掉整个 mod 不加载 | 排查困难 | 阶段 C | 一次只改一个文件，改完就进游戏验 |
| R5 | `ConditionTimeAfterShot` 语义与预期相反 | 手感不对但不致命 | 阶段 A | 调 `Duration`，或改触发方式 |
| R6 | `[Config]` 哈希与当前游戏版本不符 | mod 不加载 / 提示不兼容 | 第一次进游戏 | 从同版本 mod 抄哈希 |
| R7 | 新增对象 GUID 冲突 | 加载异常或对象被覆盖 | 阶段 C | 每个新对象用新 GUID，别抄原对象的 |
| R8 | `AltitudeMinGRU` 压不下去 | 俯冲变平飞 | 阶段 C 调参 | 接受为「低空平飞」，功能不减 |

**R2 特别标注**：这 14 个运输直升机的 `NbSeatsAvailable = 1`、`LoadRadiusGRU = 70`。
70 GRU 大约是一个车身长度。Pero 停在机场停机坪，直升机要落到几乎重叠才够得着。
**如果阶段 B 不出现「装载」菜单，先怀疑这个，再怀疑路径类型。**

---

## 9. 一句话判定

```
阶段 A 通过  → 「开火后自毁」这条链成立，FPV 的「一次性」有了
阶段 B 通过  → 「飞机能被运」这条成立，D1 成立，FPV 的出场有了
两者都过    → 阶段 C 只是组装，剩下的全是调参和美术
阶段 B 失败  → 退 D2：不做运输，用 DeploymentShiftGRU 从地图外冲进来
```

**别跳过 B 直接做 C。** C 要跨三个文件新增四个对象，如果 B 不成立，
C 做出来的东西只能当固定炮台用 —— 那是另一件事了。
