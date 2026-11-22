"""Fetch a pinned official Pokemon Showdown distribution for local legality QA.

This is an optional developer tool, not an application dependency.
"""

import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request


if __name__ == "__main__":
    root = Path(__file__).parent
    metadata = json.load(urllib.request.urlopen("https://registry.npmjs.org/pokemon-showdown/0.11.11", timeout=45))
    archive = urllib.request.urlopen(metadata["dist"]["tarball"], timeout=45).read()
    if hashlib.sha256(archive).hexdigest() != "49af14aaed1084887f756372c7386c9987905930cc848ed31e9202c10c592d66":
        raise ValueError("Pinned Pokemon Showdown archive checksum mismatch")
    target = root / ".tools" / "pokemon-showdown"
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        package.extractall(target, filter="data")
    dependency = json.load(urllib.request.urlopen("https://registry.npmjs.org/ts-chacha20/1.2.0", timeout=45))
    dependency_archive = urllib.request.urlopen(dependency["dist"]["tarball"], timeout=45).read()
    if hashlib.sha256(dependency_archive).hexdigest() != "99e69b9d289bfcb51acf930925ce862dbfad8589a67144b07d60173441dca1b8":
        raise ValueError("Pinned ts-chacha20 archive checksum mismatch")
    dependency_target = target / "package" / "node_modules" / "ts-chacha20"
    dependency_target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(dependency_archive), mode="r:gz") as package:
        for member in package.getmembers():
            if member.name.startswith("package/"):
                member.name = member.name[len("package/"):]
                if member.name:
                    package.extract(member, dependency_target, filter="data")
    record = {"package": metadata["name"], "version": metadata["version"],
              "url": metadata["dist"]["tarball"], "sha256": hashlib.sha256(archive).hexdigest(),
              "source": "https://github.com/smogon/pokemon-showdown",
              "checked_on": "2026-11-05",
              "validator_dependency": {"package": "ts-chacha20", "version": "1.2.0",
                                       "url": dependency["dist"]["tarball"],
                                       "sha256": hashlib.sha256(dependency_archive).hexdigest()}}
    (root / "data" / "smogon" / "validator_source.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({**record, "dependencies": metadata.get("dependencies")}))
