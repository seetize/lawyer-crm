import os
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
runpy.run_module("app.bot", run_name="__main__")
