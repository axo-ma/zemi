"""ZEMI platform library."""

import os
import sys


if os.name == "nt":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

from . import arsenal, env, params, playbook, toml
from .params import ModuleOptimizer, ParamSample, ParamSampler, ParamSpace, PlaybookOptimizer
from .dataset import TableDetectionTrialDataset, TrialDataset
from .sample_trial import SampleTrial, TableDetectionSampleTrial
from .inputs import InputStore
from .component import ComponentReport, Module, Playbook, ZemiComponent
from .playbook import output_dir, output_params, output_path

__all__ = [
    "ComponentReport",
    "Module",
    "Playbook",
    "ZemiComponent",
    "arsenal",
    "env",
    "params",
    "ParamSample",
    "ParamSampler",
    "ParamSpace",
    "PlaybookOptimizer",
    "ModuleOptimizer",
    "TrialDataset",
    "TableDetectionTrialDataset",
    "SampleTrial",
    "TableDetectionSampleTrial",
    "InputStore",
    "output_dir",
    "output_params",
    "output_path",
    "playbook",
    "toml",
]
