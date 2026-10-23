"""Build native setup + launcher using only Python stdlib and Windows .NET.

The payload is an explicit allowlist; research data, local environments, and test
fixtures can never be bundled by a recursive repository archive.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import tomllib
import zipfile


def build(root, output):
    if os.name != "nt":
        raise RuntimeError("Build this Windows release on Windows.")
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.exists():
        raise RuntimeError("The Windows .NET Framework compiler is not installed.")
    config = json.loads((root / "windows/release.json").read_text())
    config["version"] = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    # Keep native branding aligned with the browser on every future release.
    css = (root / "review_extractor/static/style.css").read_text(encoding="utf-8")
    palette = {}
    for name, pattern in {
        "green": r"--green:(#[0-9a-fA-F]{6})",
        "ink": r"--ink:(#[0-9a-fA-F]{6})",
        "pale": r"--pale:(#[0-9a-fA-F]{6})",
        "accent": r"--accent:(#[0-9a-fA-F]{6})",
        "background": r":root\{[^}]*?background:(#[0-9a-fA-F]{6})",
    }.items():
        match = re.search(pattern, css)
        if not match:
            raise ValueError("Browser brand color not found: " + name)
        palette[name] = match.group(1)
    config["colors"] = palette
    inputs = [p for p in (root / "review_extractor").rglob("*")
              if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}]
    inputs += [root / name for name in ("pyproject.toml", "requirements.txt", "requirements-extras.txt", "README.md", "MANIFEST.in",
                                       "THIRD_PARTY_NOTICES.md", "WINDOWS_SETUP.md")]
    inputs += [root / "windows" / name for name in ("installer.py", "runner.py", "validate.py", "models.py", "Desktop.cs", "release.json", "build_icon.ps1")]
    inputs += [root / "scripts/build_windows.py"]
    fingerprint = hashlib.sha256()
    for path in sorted(inputs):
        fingerprint.update(path.relative_to(root).as_posix().encode())
        fingerprint.update(path.read_bytes())
    config["payload_id"] = fingerprint.hexdigest()
    refs = ["System.dll", "System.Core.dll", "System.Drawing.dll", "System.Windows.Forms.dll",
            "System.Web.Extensions.dll", "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll",
            "Microsoft.CSharp.dll"]
    common = [str(compiler), "/nologo", "/optimize+", "/target:winexe", "/platform:anycpu"]
    common += ["/reference:" + ref for ref in refs]
    with tempfile.TemporaryDirectory(prefix="systematic-build-", dir=output) as temp:
        work = Path(temp)
        release = work / "release.json"
        release.write_text(json.dumps(config, indent=2), encoding="utf-8")
        # A small native brand icon; source-controlled drawing, no downloaded assets.
        icon = work / "systematic.ico"
        icon_script = root / "windows/build_icon.ps1"
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(icon_script), "-OutputPath", str(icon),
                        "-ReleasePath", str(release)], check=True)
        common += ["/win32icon:" + str(icon), "/resource:" + str(icon) + ",systematic.ico"]
        launcher = output / "Systematic.exe"
        subprocess.run(common + ["/define:LAUNCHER", "/out:" + str(launcher),
                                "/resource:" + str(release) + ",release.json", str(root / "windows/Desktop.cs")], check=True)
        payload = work / "payload.zip"
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(inputs):
                if path.parent in (root / "windows", root / "scripts"):
                    continue
                archive.write(path, "project/" + path.relative_to(root).as_posix())
            for name in ("installer.py", "runner.py", "validate.py", "models.py"):
                archive.write(root / "windows" / name, name)
            archive.write(release, "release.json")
            archive.write(launcher, "Systematic.exe")
            archive.write(icon, "Systematic.ico")
        setup = output / "Systematic-Setup.exe"
        subprocess.run(common + ["/out:" + str(setup), "/resource:" + str(payload) + ",payload.zip",
                                "/resource:" + str(release) + ",release.json", str(root / "windows/Desktop.cs")], check=True)
    (output / "release.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (output / "SHA256SUMS.txt").write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        for path in (setup, launcher)), encoding="utf-8")
    print(setup)
    return setup


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/windows"))
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.output)
