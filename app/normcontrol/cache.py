"""Publish immutable stage outputs only after their producer has finished."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def link_or_copy(src, dst):
    try:
        os.link(src,dst)
    except OSError:
        shutil.copyfile(src,dst)


def stage(root, namespace, identity, produce):
    parent = root/"cache"/namespace
    parent.mkdir(parents=True,exist_ok=True)
    final = parent/fingerprint(identity)
    if final.is_dir():
        return final,True
    with TemporaryDirectory(dir=parent) as temp:
        candidate = Path(temp)/"ready"
        candidate.mkdir()
        produce(candidate)
        try:
            candidate.rename(final)
        except OSError:
            if not final.is_dir():
                raise
    return final,False
