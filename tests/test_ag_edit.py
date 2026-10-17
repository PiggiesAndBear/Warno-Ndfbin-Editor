# -*- coding: utf-8 -*-
"""AG 属性写入（clone-on-write）的验收测试。

标准直接来自 `doc/AG牌组属性编辑-实现计划.md` §6 P1：

* **写隔离** —— 改一个营之后，共用同一模块的其它营必须**逐营逐属性一字未变**；
* **幂等** —— 不改任何东西直接保存，字节不变；
* **结构不退化** —— 改完仍能解码，对象数只增不减，克隆体是新 id。

全部在临时副本上做，不碰 mod 里的原文件。

    python -m tests.test_ag_edit
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env                                              # noqa: E402

from ndfbin_decks.ndfedit import ag_edit, ag_view        # noqa: E402
from ndfbin_decks.ndfedit.schema import NdfDocument      # noqa: E402

PAWN = _env.mod_dir(_env.BERLIN) / "Gen" / "NDF" / "GFX" / "Pawn.ndfbin"

PASS: list[str] = []
FAIL: list[str] = []

#: 实测：值 12.0 的那个防空模块，被 126 个营共用。
SHARED_AIR_MODULE = 1240


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          + (f"  {detail}" if detail else ""))


def attr_of(key: str) -> ag_view.Attr:
    return next(a for a in ag_view.ATTRS if a.key == key)


def main() -> int:
    if not PAWN.is_file():
        _env.skip(f"这个 mod 没有 Pawn.ndfbin（{PAWN}）")

    tmp = Path(tempfile.mkdtemp(prefix="ag_edit_test_"))
    dst = tmp / "Pawn.ndfbin"
    try:
        shutil.copy2(PAWN, dst)
        original_md5 = hashlib.md5(dst.read_bytes()).hexdigest()

        nd = NdfDocument.load(dst)
        pawns = ag_view.build_pawns(nd)
        air = attr_of("air_deny")
        n_objects = len(nd.decoded["objects"])

        # ---- 1. 幂等 ----
        print("=== 1. 不改就保存，字节不变 ===")
        before = dst.read_bytes()
        nd.save()
        check("无改动保存后 md5 不变",
              hashlib.md5(dst.read_bytes()).hexdigest() == original_md5,
              original_md5)

        # ---- 2. 整组命中：应该直接改模块，不克隆 ----
        print()
        print("=== 2. 整组命中 → 直接改模块，不克隆 ===")
        members = [p for p in pawns
                   if p.modules.get(air.module) == SHARED_AIR_MODULE]
        check(f"模块 #{SHARED_AIR_MODULE} 有 126 个营在用",
              len(members) == 126, str(len(members)))
        res = ag_edit.apply_attr(nd, members, air, 15.0)
        check("没有克隆", res.cloned == 0, str(res.cloned))
        check("126 个营都算改动", res.changed == 126, str(res.changed))
        check("1 个模块原地改", res.in_place == 1, str(res.in_place))

        # ---- 3. 部分命中：应该每个营克隆一份 ----
        print()
        print("=== 3. 部分命中 → 每个营克隆一份 ===")
        three = members[:3]
        res2 = ag_edit.apply_attr(nd, three, air, 20.0)
        check("克隆了 3 个", res2.cloned == 3, str(res2.cloned))
        check("只改 3 个", res2.changed == 3, str(res2.changed))
        check("对象数 +3", len(nd.decoded["objects"]) == n_objects + 3,
              f"{len(nd.decoded['objects'])} vs {n_objects + 3}")

        # ---- 4. 落盘 + 重载 ----
        print()
        print("=== 4. 保存后重载，逐营核对 ===")
        nd.save()
        nd2 = NdfDocument.load(dst)
        pawns2 = ag_view.build_pawns(nd2)
        check("重载后仍能解码", len(pawns2) == len(pawns),
              f"{len(pawns2)} vs {len(pawns)}")

        picked = {p.identifier for p in three}
        values = {p.identifier: p.values.get("air_deny") for p in pawns2}
        check("被单独克隆的 3 个营 = 20.0",
              all(values.get(k) == 20.0 for k in picked),
              str({k: values.get(k) for k in list(picked)[:3]}))
        rest = [v for k, v in values.items()
                if k not in picked and v is not None]
        check("其余有防空半径的营 = 15.0（整组改的结果）",
              all(v == 15.0 for v in rest if v != 6.599999904632568
                  and v != 36.0),
              f"{len(rest)} 个，取值 {sorted(set(rest))}")
        others = [v for v in rest if v in (6.599999904632568, 36.0)]
        check("另外两组没被误伤（6.6 / 36.0 原样保留）",
              len(others) == 4, str(len(others)))

        # ---- 5. 单独改一个营，其余一字不动 ----
        print()
        print("=== 5. 单营改：其余营逐属性核对 ===")
        dst2 = tmp / "Pawn2.ndfbin"
        shutil.copy2(PAWN, dst2)
        nd3 = NdfDocument.load(dst2)
        pawns3 = ag_view.build_pawns(nd3)
        target = next(p for p in pawns3
                      if p.modules.get(air.module) == SHARED_AIR_MODULE)
        snapshot = {p.identifier: dict(p.values) for p in pawns3}
        one = ag_edit.set_attr(nd3, target, air, 30.0)
        check("单营改触发了克隆", one.cloned, str(one.cloned))
        check("报告说原模块被 126 个营共用", one.shared == 126, str(one.shared))

        nd3.save()
        pawns4 = ag_view.build_pawns(NdfDocument.load(dst2))
        after = {p.identifier: dict(p.values) for p in pawns4}
        changed = [k for k in snapshot if snapshot[k] != after.get(k)]
        check("只有目标营变了", changed == [target.identifier],
              f"{len(changed)} 个：{changed[:3]}")
        check("目标营 = 30.0",
              after[target.identifier].get("air_deny") == 30.0,
              str(after[target.identifier].get("air_deny")))

        # ---- 6. 错误路径 ----
        print()
        print("=== 6. 没有这个字段时给一句人话 ===")
        try:
            ag_edit.apply_attr(nd3, [pawn for pawn in pawns4
                                     if "air_deny" not in pawn.values][:5],
                               air, 1.0)
            check("对没有该字段的营不崩", True, "跳过")
        except Exception as exc:                        # noqa: BLE001
            check("对没有该字段的营不崩", False, repr(exc))
            print(f"        {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAIL:
        print(f"FAIL — {len(FAIL)} / {len(PASS) + len(FAIL)} 项未通过：")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
