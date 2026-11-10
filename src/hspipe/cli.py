"""Command-line interface:  hsp <command> ..."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__

GITIGNORE = """# Never commit participant data or outputs
data/
output/
*.sav
*.zsav
*.dta
*.xlsx
*.csv
!*_template.csv
"""


def _print_summary(ctx) -> None:
    issues = ctx.tables.get("issues")
    n_err = int((issues["severity"] == "error").sum()) if issues is not None and not issues.empty else 0
    n_warn = int((issues["severity"] == "warning").sum()) if issues is not None and not issues.empty else 0
    print(f"  raw:   {ctx.raw_shape[0]} rows x {ctx.raw_shape[1]} columns")
    print(f"  clean: {ctx.df.shape[0]} rows x {ctx.df.shape[1]} columns")
    print(f"  validation: {n_err} errors, {n_warn} warnings")
    for s in ctx.scales:
        alpha = "" if s["alpha"] != s["alpha"] else f", alpha={s['alpha']:.2f}"
        print(f"  scale {s['scale']}: n={s['n_scored']}, mean={s['mean']:.2f}{alpha}")
    for e in ctx.log:
        if e["step"].endswith("warning"):
            extra = "; ".join(f"{k}={v}" for k, v in e.items() if k not in {"step", "detail"})
            print(f"  ! {e['detail']}: {extra}")
    outputs = getattr(ctx, "outputs", {})
    if outputs:
        print("  outputs:")
        for k, v in outputs.items():
            print(f"    {k:<20} {v}")


def cmd_init(args) -> int:
    from .config import template_path
    root = Path(args.directory)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "output").mkdir(exist_ok=True)
    dest = root / "config.yaml"
    if dest.exists() and not args.force:
        print(f"{dest} already exists (use --force to overwrite)")
        return 1
    shutil.copy(template_path(), dest)
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8")
    print(f"Created {dest}\nPut your export and data dictionary in {root / 'data'}, edit config.yaml, then run:\n"
          f"  hsp run {dest}")
    return 0


def cmd_run(args) -> int:
    from .config import load_config
    from .pipeline import run
    cfg = load_config(args.config)
    if args.output:
        cfg["project"]["output_dir"] = str(Path(args.output).resolve())
    for flag in ("html", "excel", "spss"):
        if getattr(args, f"no_{flag}", False):
            cfg["outputs"][flag] = False
    print(f"health-survey-pipeline {__version__} – {cfg['project']['name']}")
    ctx = run(cfg)
    _print_summary(ctx)
    return 0


def cmd_check(args) -> int:
    from .config import load_config
    from .pipeline import run
    cfg = load_config(args.config)
    ctx = run(cfg, write=False)
    _print_summary(ctx)
    issues = ctx.tables["issues"]
    if not issues.empty:
        print("\n  top issues:")
        top = issues.groupby(["rule", "variable"]).size().sort_values(ascending=False).head(15)
        for (rule, var), n in top.items():
            print(f"    {n:>5}  {rule:<18} {var}")
    return 0


def cmd_profile(args) -> int:
    from .pipeline import run
    out = Path(args.output or (Path(args.data).parent / "profile_output"))
    raw = {
        "project": {"name": f"Profile of {Path(args.data).name}", "output_dir": str(out.resolve())},
        "input": {"data": str(Path(args.data).resolve()), "id_column": args.id,
                  "data_dictionary": str(Path(args.dictionary).resolve()) if args.dictionary else None,
                  "event_column": args.event},
        "validation": {"on_invalid": "flag"},
        "outputs": {"spss": False},
    }
    ctx = run(raw)
    _print_summary(ctx)
    return 0


def cmd_demo(args) -> int:
    from .config import load_config
    from .pipeline import run
    from .synth import make_demo
    root = Path(args.directory)
    info = make_demo(root / "data", n=args.n, seed=args.seed)
    cfg_path = root / "config.yaml"
    shutil.copy(Path(__file__).parent / "templates" / "demo_config.yaml", cfg_path)
    print(f"Synthetic demo data written to {root / 'data'} ({info['rows']} rows, all fake)")
    cfg = load_config(cfg_path)
    try:
        import pyreadstat  # noqa: F401
    except ImportError:
        cfg["outputs"]["spss"] = False
    ctx = run(cfg)
    _print_summary(ctx)
    print(f"\nOpen {Path(cfg['project']['output_dir']) / 'report.html'} in a browser.")
    return 0


def cmd_instruments(_args) -> int:
    from .scoring import INSTRUMENTS
    for key, d in INSTRUMENTS.items():
        rev = f", reverse items {d['reverse']}" if d.get("reverse") else ""
        print(f"  {key:<7} {d['label']}: {d['n_items']} items scored {d['item_range'][0]}-{d['item_range'][1]}, "
              f"{d['method']}{rev}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hsp", description="Clean, validate, score and describe survey / REDCap / "
                                "EMR exports from a YAML config.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create a project folder with a commented config.yaml")
    s.add_argument("directory", nargs="?", default=".")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("run", help="run the pipeline and write all outputs")
    s.add_argument("config")
    s.add_argument("-o", "--output", help="override project.output_dir")
    s.add_argument("--no-html", action="store_true")
    s.add_argument("--no-excel", action="store_true")
    s.add_argument("--no-spss", action="store_true")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("check", help="dry run: validate config and data, print issues, write nothing")
    s.add_argument("config")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("profile", help="quick report on any file without writing a config")
    s.add_argument("data")
    s.add_argument("--dictionary", "-d")
    s.add_argument("--id", default="record_id")
    s.add_argument("--event", default=None)
    s.add_argument("--output", "-o")
    s.set_defaults(func=cmd_profile)

    s = sub.add_parser("demo", help="generate a synthetic REDCap-style project and run the pipeline on it")
    s.add_argument("directory", nargs="?", default="hsp_demo")
    s.add_argument("-n", type=int, default=240, help="number of fake participants")
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("instruments", help="list built-in scoring definitions")
    s.set_defaults(func=cmd_instruments)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, ImportError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
