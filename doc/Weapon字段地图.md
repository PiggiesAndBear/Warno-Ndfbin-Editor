# Weapon.ndfbin 字段地图（面向「所见即所改」UI）

> **用途**：把二进制对象图映射为玩家能理解的编辑项。
> 这是编辑器 UI 的**数据依据**——每个可编辑项都要能追溯到本文的一条路径。
>
> **样本**：`workshop/content/1611600/3388575848/Gen/NDF/GFX/Weapon.ndfbin`
> 265,675 B → 解压 4,016,444 B → **24,168 对象 / 57 类 / 274 属性**
> 解码耗时 0.87 s（`ndfbin_decks.core.cndf_codec.decode`）

---

## 一、核心结论：伤害/射程/射速全在同一个类

**`TAmmunitionDescriptor`（1,548 个实例）承载了几乎所有玩家关心的数值。**

这意味着 UI 不需要让玩家穿越五层对象树——
只要能列出 1,548 个弹药，按名称筛选，然后展示关键字段即可。

---

## 二、完整链路（已实测走通）

```
TEntityDescriptor                      312   单位级根
  └─ ModulesDescriptors (list)
       └─ TWeaponManagerModuleDescriptor   3,228  ★ 武器主体
            ├─ _ShortDatabaseName (strg_ref)
            │     └─ 'WeaponDescriptor_2K11_KRUG_DDR'   ← 玩家看到的名字
            └─ TurretDescriptorList (list)
                 └─ TTurretTwoAxisDescriptor       3,323  炮塔
                      ├─ AngleRotationMax/MinPitch/MaxPitch (float32)  ← 炮塔俯仰范围
                      ├─ VitesseRotation (float32)                    ← 转速
                      └─ MountedWeaponDescriptorList (list)
                           └─ TMountedWeaponDescriptor     8,995  挂载武器
                                ├─ NbWeapons (int32)                ← 武器数量
                                ├─ HandheldEquipmentKey (strg_ref)  ← 'WeaponAlternative_1'
                                └─ Ammunition (obj_ref) ★★★
                                     └─ TAmmunitionDescriptor  1,548
                                          ★ 伤害/射程/射速/装弹量全在这里
```

**实测单例**（`WeaponDescriptor_2K11_KRUG_DDR`）：

| 层级 | id | 说明 |
|---|---|---|
| WeaponManager | 4121 | `_ShortDatabaseName` |
| Turret | 4122 | |
| Mount | 4123 | `NbWeapons=1` |
| **Ammunition** | **1506** | ← 编辑目标 |

---

## 三、★ 玩家关心的字段 → 实际路径

全部位于 `TAmmunitionDescriptor`（下表「属性 ID」即 `property_id`）。

### 3.1 伤害

| 玩家语言 | 属性名 | prop_id | 类型 | 2K11 实测值 |
|---|---|---|---|---|
| 物理伤害 | `PhysicalDamages` | 57 | float32 | **13.0** |
| 压制伤害 | `SuppressDamages` | 60 | float32 | **1200.0** |
| 溅射伤害半径 | `RadiusSplashPhysicalDamagesGRU` | 56 | float32 | 159.0 |
| 溅射压制半径 | `RadiusSplashSuppressDamagesGRU` | 59 | float32 | 286.0 |
| UI 显示伤害 | `ShowDamageInUI` | 58 | bool | True |

> **命名陷阱**：官方字段是**法语的** `PhysicalDamages`（物理=Physical，
> 但在法语里 `Physique`）。压制 = `SuppressDamages`（= Suppression 压制）。
> 玩家看到的应该是「物理伤害 / 压制伤害」。

### 3.2 射程

| 玩家语言 | 属性名 | prop_id | 类型 | 2K11 实测值 |
|---|---|---|---|---|
| **最大射程** | `MaximumRangeGRU` | 132 | float32 | 6,000.0 |
| **最小射程** | `MinimumRangeGRU` | 133 | float32 | — |
| 对直升机射程 | `MaximumRangeHelicopterGRU` | 131 | float32 | — |
| 对飞机射程 | `MaximumRangeAirplaneGRU` | 53 | float32 | 6,000.0 |
| 对地射程 | `MaximumRangeProjectileGRU` | 178 | float32 | — |
| 弹丸速度 | `ProjectileSpeedGRU` | 63 | float32 | 5,300.0 |
| 近距散布 | `DispersionAtMinRangeGRU` | 134 | float32 | — |
| 远距散布 | `DispersionAtMaxRangeGRU` | 135 | float32 | — |
| 整体散布角 | `AngleDispersion` | 54 | float32 | 0.002 |

> `GRU` 后缀 = **G**raph **U**nit，游戏中实际使用「格」为单位。
> 界面上应显示为「格」而非原始浮点数。

### 3.3 射速 / 装弹量

| 玩家语言 | 属性名 | prop_id | 类型 | 2K11 实测值 |
|---|---|---|---|---|
| **射击间隔** | `TimeBetweenTwoShots` | 51 | float32 | **6.0** |
| **连发间隔** | `TimeBetweenTwoSalvos` | 67 | float32 | 30.0 |
| 连发间隔(最小) | `TimeBetweenTwoSalvos_Min` | 68 | float32 | — |
| 连发间隔(最大) | `TimeBetweenTwoSalvos_Max` | 69 | float32 | — |
| **每轮弹量** | `ShotsCountPerSalvo` | 71 | int32 | **2** |
| **同时弹丸数** | `SimultaneousShotsCount` | 70 | int32 | **1** |
| 瞄准时间 | `AimingTime` | 136 | float32 | 1.0 |
| 移动中可射击 | `CanShootWhileMoving` | 79 | bool | — |

> **DPS 计算（UI 可直接给出）**：
> `每轮弹量 × 同时弹丸数 ÷ 射击间隔`
> 2K11：`2 × 1 ÷ 6 = 0.33 发/秒`
>
> `TimeBetweenTwoShots` 与 `TimeBetweenTwoSalvos` 语义不同：
> 前者=两发子弹之间，后者=两轮连发之间。UI 必须区分，否则玩家会误改。

### 3.4 补给与消耗

| 玩家语言 | 属性名 | prop_id | 类型 | 2K11 实测值 |
|---|---|---|---|---|
| 补给消耗 | `SupplyCost` | 78 | float32 | 200.0 |
| 噪声累积上限 | `ShotsBeforeMaxNoise` | 66 | int32 | — |
| 噪声衰减惩罚 | `NoiseDissimulationMalus` | 65 | float32 | — |

### 3.5 弹药标识（用于列表显示）

| 字段 | 属性名 | prop_id | 类型 | 说明 |
|---|---|---|---|---|
| 弹药名 | `Name` | 40 | **loc_hash** | ⚠️ 本地化哈希，需 TRAN 表才能显示 |
| 口径 | `Caliber` | 41 | loc_hash | 同上 |
| 类别 | `TypeCategoryName` | 42 | loc_hash | 同上 |
| 描述 token | `WeaponDescriptionToken` | 43 | loc_hash | 同上 |
| 界面图标 | `InterfaceWeaponTexture` | 74 | strg_ref | 可直接显示 |

> **⚠️ UI 关键问题**：`Name`/`Caliber` 是 `loc_hash`（本地化哈希），
> **不是**明文字符串。要显示「2K11 的名字」必须查 TRAN 表（3,707 条）。
> 如果某个弹药在 TRAN 里查不到，UI 必须有明确的 fallback
> （例如显示 `DescriptorId` 或 `Ammunition_<id>`），
> **不能静默显示空白**——这是现有工具最可能让玩家困惑的地方。

---

## 四、其他类速查（玩家可能也会想改）

| 类 | 实例数 | 玩家视角 | 关键属性 |
|---|---|---|---|
| `TMountedWeaponDescriptor` | 8,995 | 武器位 | `NbWeapons`(247) 武器数、`HandheldEquipmentKey`(242) 切换键 |
| `TTurretTwoAxisDescriptor` | 3,323 | 炮塔 | `AngleRotationMax/MinPitch/MaxPitch`(236-238) 俯仰、`VitesseRotation`(235) 转速 |
| `TTurretInfanterieDescriptor` | 2,769 | 步兵武器架 | `MountedWeaponDescriptorList`(266) |
| `TTurretUnitDescriptor` | 1,546 | 单位炮塔 | 同上结构 |
| `TEntityDescriptor` | 312 | 单位 | `ModulesDescriptors`(86) 模块列表 |
| `TBaseDamageModuleDescriptor` | 4 | 伤害上限 | `MaxPhysicalDamages`(99)、`MaxStunDamages`(100) |
| `TDamageModuleDescriptor` | 3 | 伤害行为 | `KillWhenDamagesReachMax`(101) |
| `TDepictionDescriptor` | 309 | 外观 | `DepictionAlternatives`(115) |
| `TTagsModuleDescriptor` | 299 | 标签 | `TagSet`(88) |

---

## 五、UI 设计建议（基于以上数据）

### 5.1 不要做成通用对象树编辑器

24,168 个对象、274 个属性，如果给玩家一个原始树，
**没人能找到 2K11 的伤害值在哪**。这正是当前体验的痛点。

### 5.2 建议的三层界面

```
第 1 层：武器列表（按玩家认知组织）
    搜索框：「搜武器名 / 弹药名 / 口径」
    列表项显示：武器名 + 弹药类型图标 + 当前伤害/射程摘要
    ↑ 需要解决 loc_hash → 文本的映射（见 3.5）

第 2 层：弹药编辑面板（核心界面）
    直接展示 TAmmunitionDescriptor 的关键字段，分组：
    ┌─ 伤害 ─────────────┐  ┌─ 射程 ─────────────┐
    │ 物理伤害    [ 13 ]   │  │ 最大射程    [6000]   │  ← 带「格」单位
    │ 压制伤害   [1200]   │  │ 最小射程       [ ]  │
    │ 溅射半径    [159 ]   │  │ 弹丸速度   [5300]   │
    └───────────────────┘  └───────────────────┘
    ┌─ 射速 ─────────────┐  ┌─ 补给 ─────────────┐
    │ 射击间隔    [ 6 ]s  │  │ 补给消耗    [200]   │
    │ 每轮弹量    [ 2 ]   │  └───────────────────┘
    │ 同时弹丸    [ 1 ]   │
    │ → 自动算出 DPS 0.33 │  ← 派生显示，帮玩家理解
    └───────────────────┘
    底部：实时预览「改动影响」提示

第 3 层：高级（折叠区，默认收起）
    散布参数、噪声、导弹配置、命中判定等
```

### 5.3 必须做的三件事

| # | 事项 | 原因 |
|---|---|---|
| 1 | **loc_hash → 文本解析** | 弹药名/口径全是哈希，不解析界面全是空白 |
| 2 | **单位换算（GRU → 格）** | 直接显示 6000.0 玩家看不懂 |
| 3 | **派生值计算（DPS、每分钟发数）** | 帮玩家理解字段之间的关系 |

### 5.4 参考实现（已验证）

```python
# 取某个武器的全部关键数值
file, res = cndf_codec.decode(open('Weapon.ndfbin','rb').read())
by_id = {o['id']: o for o in res['objects']}

def prop(obj, name):
    for p in obj['properties']:
        if p['property_name'] == name:
            return p['value']
    return None

# obj_ref 解引用：id 在 'object_id' 键下
# list 解引用：元素在 'items' 键下，每项带 'object_id' + 'class_id'
ammo = by_id[prop(mount, 'Ammunition')['object_id']]

dmg  = prop(ammo, 'PhysicalDamages')['value']      # 13.0
rng  = prop(ammo, 'MaximumRangeGRU')['value']      # 6000.0
rof  = prop(ammo, 'TimeBetweenTwoShots')['value']  # 6.0
per  = prop(ammo, 'ShotsCountPerSalvo')['value']   # 2
sim  = prop(ammo, 'SimultaneousShotsCount')['value']# 1
dps  = per * sim / rof if rof else 0
```

> **注意**：list 元素的引用在 `items` 键下，元素直接带 `object_id`；
> 而标量 obj_ref 的 id 也在 `object_id` 下。两者键名相同，位置不同——
> 这是现有 `cndf_codec` 的实际约定，UI 封装层要统一处理。

---

## 六、待补充

| 项 | 状态 |
|---|---|
| TRAN 表 → 中文/英文文本解析 | ⬜ 未实现（3,707 条可用） |
| 其余 11 个 ndfbin 的字段地图 | ⬜ 未做（Unit.ndfbin 650KB、Division.ndfbin 等） |
| Deck.ndfbin 的字段地图 | ⬜ 未做（该文件是 deck 专用格式） |
| loc_hash 缺失时的 fallback 策略 | ⬜ 待定 |

---

**数据来源**：直接对真实 mod 文件解码后统计，非推测。
**复核方式**：`ndfbin_decks.core.cndf_codec.decode()` 在
`C:\Users\123\Documents\GitHub\Warno Ndfbin Editor` 下可完整重现上述全部数值。