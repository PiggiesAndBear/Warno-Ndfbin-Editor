# FPV 攻击无人机 —— 可行性勘察（基于 base.zip 原版源码）

> 目标：做一个俄乌战场的 FPV 攻击小组，武器是一架**可选中、可操作、自杀式**的无人机
> 依据：`Mods/ModData/base.zip` 的 4564 个 `.ndf` 源码（出厂状态，不含任何 mod）
> 结论：**可行，不需要新机制**。但「撞上去自动爆」没有原生支持，得用「自爆技能 + 死亡爆炸」近似

---

## 一、结论先说

引擎里**四件零件全都有**，但没有一件是现成的「FPV」：

| 需求 | 引擎里的对应物 | 状态 |
|---|---|---|
| 可选中、玩家操控的无人机 | `Descriptor_Unit_Pero_UAV_SOV`（原版唯一 UAV） | ✅ 现成模板 |
| 玩家手动指定目标、只触发一次 | `CapacityTriggerType_order_target_only_once` | ✅ 枚举值 1 |
| 对目标造成物理伤害 | `TEffectInflictDamageDescriptor`（`DamageType=Physical`） | ✅ 原版用它做暴击掉血 1~5 点 |
| 攻击后自己消失 | `UnitEffect_Destruction`（= 一条 `RemoveUnit`） | ✅ 有标准包，但原版没用过 |

**缺的那一件**：没有任何「碰撞伤害」机制 —— 单位撞上单位不会自动造成伤害。
所以「撞上去就炸」只能做成「**飞到目标旁边 → 点技能引爆**」，差一层手感。

---

## 二、原型：原版 Pero 无人机

`UniteDescriptor.ndf` 里 61 处 `UAV`，但**只有一个 UAV 单位**：`Descriptor_Unit_Pero_UAV_SOV`。

```
MaxPhysicalDamages  = 2            ← 极脆
MaxSpeedInKmph      = 500
PathfindType        = $/Pathfind/PathfindTypes/Flying
UnitRole            = 'uav'
DeathExplosionAmmo  = $/GFX/Weapon/Ammo_Mort_Avion_Leger
TCapaciteModuleDescriptor → Capacite_UAV
```

`Capacite_UAV` 的做法值得直接抄：

```
Trigger           = ~/CapacityTriggerType_automatic     ← 自动
TargetTeamFilter  = ~/CapaciteTargetFilter_joueur
RangeGRU          = 0
CastTime          = 0.00
TargetEffect      = ~/UnitEffect_Malus_ciblage_UAV      ← 给敌人打标记（火力引导）
CapacityDuration  = -1.00
TargetInBuilding  = True
TargetMySelf      = True
```

**它就是一个"可被玩家选中、能飞、靠近敌人自动施加 debuff"的单位。**
FPV 要做的就是把「自动打标记」换成「手动引爆造成伤害」。

---

## 三、三条可选路径

### 路径 A（推荐）：自爆技能 + 死亡爆炸

```
玩家选中 FPV → 飞到坦克旁 → 点「引爆」→
  ① SelfEffect 对自己造成 20 点物理伤害（远超 1 点血）→ 自己死亡
  ② 死亡触发 DeathExplosionAmmo → 大威力 HEAT 弹头在落点爆炸 → 周围敌人受伤
```

**为什么推荐**：全部用现成字段，没有一处要靠猜。
`DeathExplosionAmmo` 挂在 `TCadaverModuleDescriptor`（残骸生成器）上 —— 单位一死就生成残骸，
爆炸是这个残骸生成过程的一部分，2898 个单位都在用这个字段，行为确定。

代价：爆炸威力取决于弹药表，**无法做「直击单体极高伤害 + 周围溅射」的精确区分**，
只能靠 HEAT 弹头本身的伤害模型。

### 路径 B：目标单体伤害（TargetEffect）

```
TargetEffect = UnitEffect_FPV_Hit   （TEffectInflictDamageDescriptor，DamageValue = 10~20）
SelfEffect   = UnitEffect_Destruction
```

更精确（想打谁就打谁，伤害数值可控），但**原版没有任何技能同时用 `TargetEffect` + `SelfEffect`**
（48 个技能里 `SelfEffect` 只出现 3 次，且都是单独用），
两个字段能否同时生效**必须实测**。

### 路径 C：纯武器路线

FPV = 极快的空中单位 + 射程极短（100~200 GRU）、威力大的武器，靠「贴脸开火」。
不用技能，但**不满足「一次性」** —— 弹药打完它还在，玩家可以反复用。最不像 FPV。

| | A 自爆+死亡爆炸 | B 单体伤害 | C 武器 |
|---|---|---|---|
| 一次性 | ✅ 必然消失 | ✅ 必然消失 | ❌ 弹药打完还在 |
| 伤害可控 | 中（受弹药表限制） | **高**（数值直接写） | 中 |
| 依赖实测 | 低 | **高**（两字段组合无先例） | 无 |
| 手感 | 像"自爆" | 像"点名处决" | 像"贴脸射击" |

**建议 A 做第一版，B 做增强**（如果实测两字段能共存，B 的伤害更准）。

---

## 四、实现草案

### 4.1 单位（复制 Pero_UAV 改）

```ndf
Descriptor_Unit_UKR_FPV_Team is TEntityDescriptor
(
    ClassNameForDebug = 'Unit_UKR_FPV_Team'
    ModulesDescriptors = [
        TTypeUnitModuleDescriptor( MotherCountry = 'UKR' ... )   // 见 §5 阵营
        TFormationModuleDescriptor( TypeUnitFormation = 'Recon' )
        TUnitUIModuleDescriptor
        (
            NameToken        = 'FPVTEAM'          // 需要本地化词条
            UnitRole         = 'uav'
            MenuIconTexture  = 'Texture_RTS_H_uav'
            ButtonTexture    = 'Texture_Button_Unit_FPV'
            PriceCategory    = 'C'                // 便宜、可大量
        )
        TApparenceModuleDescriptor( ReferenceMesh = $/GFX/DepictionResources/Modele_FPV )
        TGenericMovementModuleDescriptor
        (
            MaxSpeedInKmph = 800                  // 比 Pero 的 500 更快
            PathfindType   = $/Pathfind/PathfindTypes/Flying
        )
        TBaseDamageModuleDescriptor( MaxPhysicalDamages = 1 )     // 一碰就碎
        TDamageModuleDescriptor( KillWhenDamagesReachMax = True )
        TCadaverGeneratorModuleDescriptor
        (
            CadaverModuleDescriptors = [ ... ]
            DeathExplosionAmmo = $/GFX/Weapon/Ammo_FPV_Warhead   // ★ 爆炸威力在这里
        )
        TCapaciteModuleDescriptor
        (
            DefaultSkillList = [ $/GFX/EffectCapacity/Capacite_FPV_Detonate ]
        )
        TSelectionModuleDescriptor( IsSelectable = True )          // ★ 可选中
        TVisibilityModuleDescriptor( UnitConcealmentBonus = 3.0 )  // 小目标，难发现
        ...
    ]
)
```

> 更简单的做法：**直接复制 `Descriptor_Unit_Pero_UAV_SOV` 的整块**，只改上面标 ★ 的四行
> （速度、血量、死亡爆炸弹药、技能列表）。Pero 的模块组合是经过验证能跑起来的。

### 4.2 自爆技能（路径 A）

```ndf
export UnitEffect_FPV_Detonate is TEffectsPackDescriptor
(
    DescriptorId       = GUID:{...}
    NameForDebug       = 'FPV_Detonate'
    EffectsDescriptors = [
        TEffectInflictDamageDescriptor
        (
            DamageType   = ~/EDamageType/Physical
            ModifierType = ~/ModifierType_Additionnel
            DamageValue  = 20                  // 自身只有 1 点血，必然死亡
        ),
    ]
)

export Capacite_FPV_Detonate is TCapaciteDescriptor
(
    DescriptorId            = GUID:{...}
    Name                    = "FPV_Detonate"
    StackPolicy             = ~/CapacityStackPolicy_never
    Trigger                 = ~/CapacityTriggerType_order_target_only_once   // ★ 玩家下令、一次
    TargetTeamFilter        = ~/CapaciteTargetFilter_ennemi
    RangeGRU                = 200            // 200 m —— 必须贴近才能引爆，做出"撞击"感
    CastTime                = 0.00           // 零延迟，点了就炸
    CheckVisibility         = True
    CanBeCastFromTransport  = False
    SelfEffect              = ~/UnitEffect_FPV_Detonate
    CapacityDuration        = -1.00
    TargetInSelf            = False
    TargetMySelf            = False
    TargetInTransport       = False
    TargetInBuilding        = False
)
```

关键三条枚举（`CapaciteHelpers.ndf` 里查到）：

```
CapacityTriggerType_automatic              is 0    自动
CapacityTriggerType_order_target_only_once is 1    ★ 玩家下令、指定目标、只一次
CapaciteTargetFilter_ennemi                is 1    ★ 只能选敌人
```

### 4.3 弹头

`DeathExplosionAmmo` 指向一个 `TAmmunitionDescriptor`。参考原版反坦克弹的属性
（`Arme`、`Trait`、`Damage` 几项），做法与普通弹药 mod 完全一致 —— 这部分本项目已经能改。

---

## 五、需要你拍板的三件事

| # | 问题 | 选项 | 我的倾向 |
|---|---|---|---|
| 1 | **阵营** | (a) 挂在现有国家（SOV/RFA 等）；(b) 新建 `UKR` / `RUS` 国家 | **(b)** —— 但新建国家要动 `Constantes` 的国家枚举 + 旗帜/贴图，工作量比无人机本身大得多。建议第一版先挂 SOV 验证机制，跑通后再换皮 |
| 2 | **一次几架** | 1 架 / 2-3 架编成一个 pack（`NumberOfUnitInPack`） | **1 架**。多架编一队时，一架自爆后整队的选中状态会乱（`TDeploymentShift` 那套是按单单位设计的） |
| 3 | **怎么出场** | (a) pack 直接可买；(b) `AvailableTransportList`（像空降仓那样） | **(a)** pack。FPV 是独立武器平台，不是运输载具 |

---

## 六、必须实测的三点（数据无法回答）

1. **`DeathExplosionAmmo` 是不是「死亡即爆」**
   它挂在残骸生成器上，理论上单位死亡 → 生成残骸 → 爆炸。但没实测过「用技能把自己打死」这条路径。
2. **`SelfEffect` 作用于自己时会不会真的扣血**（而不是当成 buff 忽略）
   `Capacite_Choc` 是唯一先例，但它是给己方加状态，不是伤害。
3. **`RangeGRU = 200` 时点技能，单位会不会「原地爆」而不是「贴上去爆」**
   技能没有位移，玩家得自己开到 200 m 内。如果嫌不够像撞击，可以把 RangeGRU 再压到 50~100。

---

## 七、工作量

| 项 | 估量 |
|---|---|
| 复制 Pero_UAV 改 4 行 → 能选中、能飞的新单位 | 半天（含进游戏验证） |
| 自爆技能 + 伤害效果（路径 A） | 半天 |
| 弹头（HEAT/HE 参数） | 半天 |
| 图标 / 模型 / 本地化词条 | **取决于素材**，可能是最花时间的一项 |
| 进 pack、可选、能买 | 半天 |

**机制部分约 2 天**（不含美术）。风险集中在 §6 的三点，最坏情况是「自爆不生效」，
那就退到路径 B 或 C。

---

## 八、一句话总结

**能做，而且不用发明新机制** —— 原版 Pero 无人机就是「可选中、可操控」的现成模板，
`order_target_only_once` 给了「玩家点名引爆」的触发器，`InflictDamage` 给了伤害，
`DeathExplosionAmmo` 给了爆炸。

**唯一做不到的是真·碰撞伤害** —— 没有"撞上去自动炸"这种引擎行为，
所以最终手感是「飞到跟前、点一下、爆」，而不是「一头撞上去」。这一点需要提前接受。
