# 光环 mod 空降仓机制 —— 勘察笔记

> 样本：`Reclaimer Divide`（workshop `3761591570`）
> 问题：ODST 步兵乘空降仓抵达战场，部署后空降仓原地消失，原理是什么
> 结论：**空降仓是一个完整单位**，靠「运输载具 + 自带自毁技能」实现，不是引擎特效

---

## 一句话

空降仓（`Unit_UNS_ODST_POD`）是一个**装了「停车卸客 + 自毁」技能的运输载具**：

它靠 `DeploymentShiftGRU` 从 7.25 km 外**以 10000 km/h 冲进来**（注意：它是
`PathfindType=Vehicle` 的**地面载具**，不是飞机），抵达落点**停下**之后，
技能条件 `NOT(InMovement)` 成立，于是它自己施放一个 5 秒的技能：

```
槽位归零 → 乘客被卸到旁边地面 → 惯性急停 → RemoveUnit → 舱体消失
```

两个动作各由一条效应完成，都不是引擎特判：
- **卸客** = `TUnitEffectTransportSlotNumberModificationDescriptor(EffectOnTransportSlotNumber=0)`
- **消失** = `TUnitEffectRemoveUnitDescriptor`

> **本节是 2026-10-25 复核后的版本**。之前笔记里「飞行」「17 条效应」「#98/#99/#100」
> 三处有误，已在下方标明修正处。

---

## 完整链条

```
① 牌组编辑器
    ODST 班 → 运输载具选「空降仓」
    （Division.ndfbin 的 TDeckUniteRule.AvailableTransportList）

② 部署
    POD 载着 1 个 ODST，从 DeploymentShiftGRU = 7250 之外进场
    速度 MaxSpeedInKmph = 10000（普通载具是几十）
    ★ 它是地面载具（PathfindType=Vehicle），「从远处冲进来」而不是飞进来

③ 行驶到落点
    默认状态：隐形（Capacite_Invisible）+ 不可选中（Capacite_No_Select）
    ★ 途中「在移动」→ 卸客技能的条件不成立，所以不会半路把人放下来

④ 停下 —— 这是触发点，不是坐标
    技能条件 = NOT(InMovement)。载具一停，条件成立，技能开始施放
    先施放 Capacite_ODST_POD_Visible（CastTime 1 秒）
      → 隐蔽加成 -100（强制显形，玩家这才看见舱体）

⑤ 卸客 + 自毁倒计时
    施放 Capacite_ODST_POD，CastTime = 5.0 秒
      → 这 5 秒就是「舱门打开、ODST 下舱、舱体撤离」的表现时间

⑥ 效果包生效
    ODST_POD_Effect（15 条效应）一次性作用在舱体上：
      · 槽位替换为 0     TUnitEffectTransportSlotNumberModification   ← **乘客被卸下**
      · 带惯性急停        TUnitEffectStopWithInertiaEffect (UpdateEachTick=True)
      · 移除自己          TUnitEffectRemoveUnitDescriptor               ← 舱体消失
      · 其余 12 条是短暂强化（视野、射程、旋转、威胁度、影响范围）

⑦ 结果
    舱体从地图上消失，ODST 站在落点旁边
```

---

## 关键对象与字段

### 空降仓单位 `Unit_UNS_ODST_POD`（`Unit.ndfbin` #2068，47 个模块）

| 模块 | ID | 关键字段 |
|---|---|---|
| `TTransporterModuleDescriptor` | #444 | `NbSeatsAvailable=1`、`LoadRadiusGRU=70`、`TransportableTagSet=[Crew, Unite_transportable]` |
| `TTagsModuleDescriptor` | #2070 | `TagSet` 含 `Drop_Pod`、`Vehicule`、`GroundUnits` —— `CAP_ODST` 的 `AllowedTargetTags=[Drop_Pod]` 就靠它 |
| `TDeploymentShiftModuleDescriptor` | #2076 | **`DeploymentShiftGRU=7250`**（从多远外进场） |
| `TGenericMovementModuleDescriptor` | #919 | `MaxSpeedInKmph=10000`、**`PathfindType=$/Pathfind/PathfindTypes/Vehicle`**（地面载具） |
| `TLandMovementModuleDescriptor` | #920 | `UnitMovingType=2`、`TerrainSpeedFactors=$/GFX/Terrains/DropPod`（悬空） |
| `TBaseDamageModuleDescriptor` | #916 | `MaxPhysicalDamages=1.0`（一碰就碎） |
| `TDamageModuleDescriptor` | #917 | `KillWhenDamagesReachMax=True` |
| `TCapaciteModuleDescriptor` | #923 | **`DefaultSkillList` = POD / POD_Visible / No_Select / Invisible** |
| `TProductionModuleDescriptor` | #2073 | 105 点、5 秒、`FactoryType=2` |
| `TOrderConfigModuleDescriptor` | #925 | `ValidOrders=[40, 23, 26]`（只有 3 个命令，不能正常作战） |
| `TUnitUIModuleDescriptor` | #2075 | `InfoPanelConfigurationToken=VehiculeTransporter`、`ButtonTexture=…odst_pod` |

### 技能（`EffectCapacity.ndfbin`）

| ID | Name | CastTime | SelfEffect | 作用 |
|---|---|---|---|---|
| #614 | `ODST_POD` | **5.0** | → #86 | 主技能，5 秒后触发自毁 |
| #613 | `ODST_POD_Visible` | 1.0 | → #107 | 显形（隐蔽 -100） |
| #615 | `CAP_ODST` | — | → #109 | 给 ODST 打 `ODST_Inside` 标签 |
| — | `Capacite_No_Select` | — | → #105 | 不可选中 |
| — | `Capacite_Invisible` | — | — | 隐形 |

三个技能都有 `CanBeCastFromTransport=True` + `TargetInTransport=True`
—— 允许在运输载具内施放，这是「舱内 ODST 也能触发」的前提。

### 效果（`EffectCapacity.ndfbin`）

**`ODST_POD_Effect` #86**（`NameForDebug`）= **15 条**效应，按 `EffectsDescriptors` 列表顺序：

```
[ 0] IncreaseChassisRotationSpeed        ModifierType=1
[ 1] IncreaseDamageTaken                 ModifierType=1
[ 2] IncreaseDangerousness               ModifierType=1
[ 3] IncreaseVision                      ModifierType=3
[ 4] IncreaseSpecializedDetection        ModifierType=3  VisionType=2
[ 5] IncreaseInfluenceValue              ModifierType=3
[ 6] IncreaseInfluenceValueMin           ModifierType=3
[ 7] IncreaseTurretRotationSpeed         ModifierType=1
[ 8] IncreaseWeaponMaxRangeAirplane      ModifierType=3
[ 9] IncreaseWeaponMaxRange              ModifierType=3
[10] IncreaseWeaponMaxRangeHelicopter    ModifierType=3
[11] IncreaseWeaponMaxRangeProjectile    ModifierType=3
[12] TransportSlotNumberModification     ModifierType=3   ← **卸下乘客**
[13] StopWithInertiaEffect               UpdateEachTick=True ← 惯性急停
[14] RemoveUnitDescriptor                                  ← 载具消失
```

> **修正两处**：原笔记写「17 条」，实际 **15 条**；原笔记按 `#84~#100` 编号也是错的
> —— `#84` 是 `ODST_Effect #109` 里的 RaiseTag，不属于这个包。
> 正确的引用方式是按列表位置（`[12]/[13]/[14]`）或按类型，不要按 `#号`。

**`visible_Effect` #107** → #108 `IncreaseConcealmentBonus = -100.0`
隐蔽是「越高越难被发现」，**-100 就是强制暴露**。

**`ODST_Effect` #109** → `#110 RaiseTag(ODST_Inside)` + #84/#87/#88/#89/#90
给 ODST 盖上「在舱内」的标签。随后 POD 技能的 `ForbiddenTargetTags=[ODST_Inside]`
阻止重复触发 —— **防重复施放就是靠这个标签**。

---

## ★ 自动卸下乘员的机制（2026-10-25 补充勘察）

### 触发靠的是「停下」，不是坐标

`Capacite_ODST_POD`（#614）的完整字段：

```
Name                   = ODST_POD
CastTime               = 5.0
TargetMySelf           = True
SelfEffect             = -> #86 (ODST_POD_Effect)
ForbiddenTargetTags    = [ODST_Inside]
TargetInTransport      = True
CanBeCastFromTransport = True
Conditions             = [#31]
```

`#31` 展开只有一条：

```
#31  TConditionNot
        ConditionToInverse = -> #30
            #30  TConditionInMovement        ← 没有参数，就是「正在移动」
```

所以触发逻辑是：

| 载具状态 | `InMovement` | `NOT(...)` | 技能 |
|---|---|---|---|
| 正在行驶 | 成立 | 不成立 | **不触发** |
| 停下 | 不成立 | 成立 | **触发** |

**这就是「抵达指定位置」的实现方式 —— 不判断坐标，判断「停没停」。**
玩家指定落点，载具开过去自动停下，停下那一刻条件成立。落点可以是任意位置，
不需要为每个落点写脚本。

> 顺带解释了为什么途中不会半路放人：只要还在动，条件就一直不成立。

### 卸客那一条效应

```
TUnitEffectTransportSlotNumberModificationDescriptor
(
    ModifierType                 = ~/ModifierType_Remplacement    // = 3，替换
    EffectOnTransportSlotNumber  = 0
)
```

语义是**把载具的运输槽位数直接替换成 0**。乘客没有座位了，就被放到载具旁边的地面上。
`ModifierType_Pourcentage` 是另一种模式（按百分比改），这里不用。

### 原版先例：这件事有两条成熟路线

**路线 1：停车触发（ODST 用的）**

`ConditionNotInMovement` 是**原版标准件**（`ConditionsDescriptor.ndf` 里就有定义），
`CapaciteList.ndf` 里 **11 个技能**在用它，全是同一个范式：

```
Trigger        = automatic
CastTime       = 1.00 ~ 5.00
RangeGRU       = 0
TargetMySelf   = True
Conditions     = [ConditionNotInMovement]
```

| 技能 | CastTime | 干什么 |
|---|---|---|
| `Capacite_Infanterie_Dissimulee` | 1.0 | 步兵静止后加隐蔽 |
| `Capacite_Infanterie_Retranchee` | 1.0 | 步兵静止后加防御 |
| `Capacite_Tank_Dissimule` | 1.0 | 坦克静止后加隐蔽 |
| `Capacite_Trepied` | 1.0 | 架设三脚架 |
| `Capacite_GSR` | 5.0 | 地面监视雷达展开 |
| `Capacite_sniper` | 1.0 | 狙击手静止 |

所以「停车后自动做一件事」是引擎**预期用法**，不是 hack。

**路线 2：事件触发（原版卡车卸客）**

`Capacite_Debarque_camion`（`débarquer` = 法语「下车」）是原版**现成的卸客技能**：

```
Name         = "Debarque_camion"
Trigger      = automatic
CastTime     = 2.00
TargetMySelf = True
TargetEffect = ~/UnitEffect_Baisse_Dangerosite_et_Transport_0_Effet
Conditions   = [~/ConditionTagRaisedInUnit_GroundUnit_Engaged]
```

它的效果包里有三条：

```
UnitEffect_Baisse_Dangerosite_et_Transport_0_Effet
  [0] IncreaseDangerousness(Pourcentage, -90)           危险度 -90%
  [1] RaiseTag(["Camion_autodestruction", "Panique"])    打两个标签
  [2] TransportSlotNumberModification(Remplacement, 0)   ← 卸客，与 ODST 同一条
```

条件是「身上有 `GroundUnit_Engaged` 标签」（被卷入交火），
所以这是「**卡车遇敌自动把人放下来**」，不是「到点卸客」。

**两条路线的卸客动作完全是同一条效应**，只是触发器不同：

| | 触发条件 | 适合场景 |
|---|---|---|
| ODST 路线 | `NOT(InMovement)` | **到点卸客**、空投、机降 |
| 卡车路线 | `TagRaised(GroundUnit_Engaged)` | 遇敌卸客、紧急下车 |

### 复用：给任何载具加「停车卸客」

```ndf
export Capacite_My_Unload is TCapaciteDescriptor
(
    DescriptorId            = GUID:{新 GUID}
    Name                    = "My_Unload"
    StackPolicy             = ~/CapacityStackPolicy_never
    Trigger                 = ~/CapacityTriggerType_automatic
    TargetTeamFilter        = ~/CapaciteTargetFilter_joueur
    InfluenceMapAlliance    = ~/AllianceRelation/vide
    RangeGRU                = 0
    CastTime                = 1.00
    CheckVisibility         = True
    CanBeCastFromTransport  = False
    TargetEffect            = ~/UnitEffect_My_Unload_Effet
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
        ~/ConditionNotInMovement,      // ← 用原版现成的，不用自己拼 NOT
    ]
)
```

效果包（只卸客，载具留下）：

```ndf
export UnitEffect_My_Unload_Effet is TEffectsPackDescriptor
(
    DescriptorId       = GUID:{新 GUID}
    NameForDebug       = 'My_Unload_Effet'
    EffectsDescriptors = [
        TUnitEffectTransportSlotNumberModificationDescriptor
        (
            ModifierType                 = ~/ModifierType_Remplacement
            EffectOnTransportSlotNumber  = 0
        ),
    ]
)
```

要像 POD 一样**卸完连载体一起消失**，就在列表末尾加一条：

```ndf
        TUnitEffectRemoveUnitDescriptor(),
```

然后把技能挂到载具的 `TCapaciteModuleDescriptor.DefaultSkillList` 里。

### 三个必须注意的边界

1. **这个机制只管「卸」，不管「装」**
   能不能装进去仍取决于「被装单位有 `TTransportableModuleDescriptor`」+
   「载具的 `TransportableTagSet` 认它的标签」。卸客技能绕不过装载校验。

2. **`EffectOnTransportSlotNumber = 0` 是单向的**
   `ModifierType_Remplacement` 是替换，不是加减。写成 0 之后这个载具就没有座位了，
   想「卸完还能再装」不能用这个写法（需要另找按百分比修改的路径，未验证）。

3. **POD 自己就是完整模板**
   它是 `Vehicle`（地面）不是飞机，靠 `DeploymentShiftGRU=7250` + `MaxSpeedInKmph=10000`
   从地图外冲进来。如果只是想要「载具运兵进场 → 到点卸下 → 载具消失」，
   **直接照抄 POD 整套比用直升机机降简单得多**，而且每一步都有实测依据。

---

## 牌组规则（`Division.ndfbin`）

7 条 `TDeckUniteRule` 的 `AvailableTransportList` 里含 `Descriptor_Unit_UNS_ODST_POD`：

| 规则 | 作用单位 |
|---|---|
| #59 / #91 / #186 | `Unit_UNS_ODST_AT_Squad` |
| #60 / #92 / #187 | `Unit_UNS_ODST_Squad` |
| #61 | `Unit_UNS_ODST_Sniper_Squad` |

每条都同时给出：
- `AvailableWithoutTransport = True`（也可以不坐车）
- `AvailableTransportList = [ODST_POD, Warthog_Troop]`（空降仓 or 疣猪运兵车）

> **这就是为什么在 `Deck.ndfbin` 的 pack 里搜不到 POD 作运输载具** ——
> 它不走 pack 预设，走的是牌组编辑器的「可选运输载具」列表。两条路径并存。

---

## 三个设计巧思

1. **把「消失」做成单位自己的技能，而不是引擎特判**
   于是同一套机制可以复用到星盟：`Unit_COV_SquadPod` / `Unit_COV_ElitePod` /
   `Unit_UNS_LifePod` 都挂了同一组 `Capacite_ODST_POD` + `Capacite_ODST`，
   只是资源点（175 vs 105）和 `DeploymentShiftGRU`（8525.5 vs 7250）不同。

2. **隐形 + 不可选中 + 一碰就碎**
   `Capacite_Invisible` + `Capacite_No_Select` + `MaxPhysicalDamages=1.0`
   让舱体在行驶途中既不挡路也打不掉；**停下才显形**，且只能存活 5 秒。

3. **`ForbiddenTargetTags=[ODST_Inside]` 做幂等**
   没有这个标签，玩家连点两次技能会让 ODST 一起被 `RemoveUnit`。
   `CAP_ODST` 给乘客打标签，POD 技能就只对「还没载人」的舱体生效。

---

## 已知的两处数据瑕疵

| 项 | 现象 | 影响 |
|---|---|---|
| `TerrainSpeedFactors=$/GFX/Terrains/DropPod` | mod 的 `Terrains.ndfbin` 里没有 `DropPod`（只有 19 个地形）；`base.zip` 的 `.ndf` 里 grep 也是 0 处 | 悬空引用。游戏大概回退到默认速度表 —— 因为 `MaxSpeedInKmph=10000` 已经够快，不影响观感 |
| POD 不在任何 pack | 2868 个 pack 里没有 `UnitDescriptor` 或 `TransporterDescriptor` 指向它 | **不是问题**：它走 `AvailableTransportList` 路径入场，不需要 pack |

---

## 想复用到别的 mod，改哪里

| 目标 | 改什么 |
|---|---|
| **让载具停车自动卸客** | 载具 `TCapaciteModuleDescriptor.DefaultSkillList` 挂一个 `Conditions=[~/ConditionNotInMovement]` + `TargetEffect` 里含 `TUnitEffectTransportSlotNumberModificationDescriptor(0)` 的技能（见上文草案） |
| 让别的兵种也能坐空降仓 | `Division.ndfbin` 的 `TDeckUniteRule.AvailableTransportList` 加 POD |
| 换落点距离 | POD 的 `TDeploymentShiftModuleDescriptor.DeploymentShiftGRU`（7250 GRU） |
| 改自毁延迟 | `Capacite_ODST_POD` 的 `CastTime`（5.0 秒） |
| 改卸客延迟 | 同上 —— 卸客和自毁在同一个效果包里，同时发生。**要「先卸后炸」必须拆成两个技能** |
| 去掉消失 | 从 `ODST_POD_Effect` 里删掉 `RemoveUnit`（列表第 15 项），舱体会留下当空载具 |
| 换舱体外观 | `TApparenceModuleDescriptor.ReferenceMesh` → `Modele_UNS_ODST_POD` |

> 注意：`ODST_POD_Effect` 这个 effect pack 被 4 种舱共享（UNSC + 星盟），
> 改它会一次影响所有阵营。
