# 光环 mod 空降仓机制 —— 勘察笔记

> 样本：`Reclaimer Divide`（workshop `3761591570`）
> 问题：ODST 步兵乘空降仓抵达战场，部署后空降仓原地消失，原理是什么
> 结论：**空降仓是一个完整单位**，靠「运输载具 + 自带自毁技能」实现，不是引擎特效

---

## 一句话

空降仓（`Unit_UNS_ODST_POD`）是一个**装了自毁技能的运输载具**：
它从 7.25 km 外以 10000 km/h 飞入，落地后由**它自己施放一个 5 秒的技能**，
技能效果里有一条 `RemoveUnit` —— 于是舱体消失，ODST 留在原地。

「消失」不是引擎的魔法，是 `TUnitEffectRemoveUnitDescriptor` 这一条效应。

---

## 完整链条

```
① 牌组编辑器
    ODST 班 → 运输载具选「空降仓」
    （Division.ndfbin 的 TDeckUniteRule.AvailableTransportList）

② 部署
    POD 载着 1 个 ODST，从 DeploymentShiftGRU = 7250 之外进场
    速度 MaxSpeedInKmph = 10000（普通载具是几十）

③ 接近 / 落地
    默认状态：隐形（Capacite_Invisible）+ 不可选中（Capacite_No_Select）
    落地瞬间施放 Capacite_ODST_POD_Visible（1 秒）
      → 隐蔽加成 -100（强制显形，玩家这才看见舱体）

④ 自毁倒计时
    施放 Capacite_ODST_POD，CastTime = 5.0 秒
      → 这 5 秒就是「舱门打开、ODST 下舱、舱体撤离」的表现时间

⑤ 效果包生效
    ODST_POD_Effect（17 条效应）一次性作用在舱体上：
      · 运输槽位改为 0        TUnitEffectTransportSlotNumberModification (ModifierType=3)
      · 带惯性急停            TUnitEffectStopWithInertiaEffect (UpdateEachTick=True)
      · 移除自己              TUnitEffectRemoveUnitDescriptor          ← 舱体消失
      · 其余 14 条是短暂强化（视野、射程、旋转、威胁度、影响范围）

⑥ 结果
    舱体从地图上消失，ODST 站在落点
```

---

## 关键对象与字段

### 空降仓单位 `Unit_UNS_ODST_POD`（`Unit.ndfbin` #2068，47 个模块）

| 模块 | ID | 关键字段 |
|---|---|---|
| `TTransporterModuleDescriptor` | #444 | `NbSeatsAvailable=1`、`LoadRadiusGRU=70` |
| `TDeploymentShiftModuleDescriptor` | #2076 | **`DeploymentShiftGRU=7250`**（从多远外进场） |
| `TGenericMovementModuleDescriptor` | #919 | **`MaxSpeedInKmph=10000`**（飞入速度） |
| `TLandMovementModuleDescriptor` | #920 | `UnitMovingType=2`、`TerrainSpeedFactors=$/GFX/Terrains/DropPod` |
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

**`ODST_POD_Effect` #86**（`NameForDebug`）= 17 条效应：

```
#84  IncreaseChassisRotationSpeed    #92  IncreaseInfluenceValueMin
#87  IncreaseDamageTaken             #93  IncreaseTurretRotationSpeed
#88  IncreaseDangerousness           #94  IncreaseWeaponMaxRangeAirplane
#89  IncreaseVision                  #95  IncreaseWeaponMaxRange
#90  IncreaseSpecializedDetection    #96  IncreaseWeaponMaxRangeHelicopter
#91  IncreaseInfluenceValue          #97  IncreaseWeaponMaxRangeProjectile
#98  TransportSlotNumberModification (ModifierType=3)   ← 槽位归零
#99  StopWithInertiaEffect           (UpdateEachTick=True) ← 急停
#100 RemoveUnitDescriptor                                  ← 消失
```

**`visible_Effect` #107** → #108 `IncreaseConcealmentBonus = -100.0`
隐蔽是「越高越难被发现」，**-100 就是强制暴露**。

**`ODST_Effect` #109** → `#110 RaiseTag(ODST_Inside)` + #84/#87/#88/#89/#90
给 ODST 盖上「在舱内」的标签。随后 POD 技能的 `ForbiddenTargetTags=[ODST_Inside]`
阻止重复触发 —— **防重复施放就是靠这个标签**。

### 牌组规则（`Division.ndfbin`）

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
   让舱体在飞行途中既不挡路也打不掉；落地才显形，且只能存活 5 秒。

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
| 让别的兵种也能坐空降仓 | `Division.ndfbin` 的 `TDeckUniteRule.AvailableTransportList` 加 POD |
| 换落点距离 | POD 的 `TDeploymentShiftModuleDescriptor.DeploymentShiftGRU`（7250 GRU） |
| 改自毁延迟 | `Capacite_ODST_POD` 的 `CastTime`（5.0 秒） |
| 去掉消失 | 从 `ODST_POD_Effect` 里删掉 `RemoveUnit`（#100），舱体会留下当空载具 |
| 换舱体外观 | `TApparenceModuleDescriptor.ReferenceMesh` → `Modele_UNS_ODST_POD` |

> 注意：`ODST_POD_Effect` 这个 effect pack 被 4 种舱共享（UNSC + 星盟），
> 改它会一次影响所有阵营。
