"""Write the synthetic REDCap export + data dictionary into ./data (same as `hsp demo`)."""
from pathlib import Path

from hspipe.synth import make_demo

if __name__ == "__main__":
    info = make_demo(Path(__file__).parent / "data")
    print(f"wrote {info['data']} and {info['dictionary']}")
