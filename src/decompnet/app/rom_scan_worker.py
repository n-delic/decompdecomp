"""Analyse a frozen ROM Explorer request without opening the application's stores."""
from __future__ import annotations

import json
import mmap
import os
import sys
import threading
import time
from pathlib import Path

from decompnet import arch, consoles
from decompnet.app.analysis_image import verify
from decompnet.app.workers import lower_priority
from decompnet.network import NetworkError
from decompnet.rom import asset_flow, config_images, resource_catalog
from decompnet.rom.explorer import analyse


def scan(request, log, progress):
    source = request["source"]
    path = Path(source["path"])
    functions, symbols = request["functions"], request["symbols"]
    console = consoles.get(request["console"])
    segments, gp = source["segments"], source["gp"]
    big_endian = source["big_endian"]
    observation = {}
    rom = request.get("rom") or {}
    runtime_rom = path if source["fork"] else Path(request["root"]) / rom.get("program", rom.get("file", ""))
    if not path.stat().st_size:
        raise NetworkError("The analysis image is empty. Restore it or reimport before exploring.")
    with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as image:
        if source["fork"]:
            log(f"Reading your copy of the game in this forked project, {path.name}: its SHA-1 is the one recorded at import.")
        else:
            verify(image, {"root": request["root"]}, {"rom": request["rom"]})
        flow = asset_flow.trace(image, segments, functions, arch.get(source["arch"]).asset_flow,
                                contracts=console.asset_calls | request["contracts"],
                                argument_count=console.asset_argument_count, big_endian=big_endian,
                                gp=gp, cpu=console.cpu_variant, ram=console.ram, progress=progress)
        assets = (resource_catalog.regions(image)
                  + config_images.regions(image, source.get("images", []), "splat config")
                  + config_images.sheet_regions(image, source.get("sheets", []), "splat config")
                  + console.asset_regions(image, context=dict(functions=functions, symbols=symbols,
                                                              segments=segments, gp=gp, runtime_rom=str(runtime_rom),
                                                              runtime_sha1=source.get("runtime_hashes") if source["fork"] else rom.get("sha1"),
                                                              log=log, runtime_observation=observation), progress=progress))
        report = analyse(image, segments, functions, symbols, big_endian=big_endian, progress=progress,
                         ranges=[chain["source"] for chain in flow["chains"]], assets=assets)
        if observation:
            report["runtime_observation"] = observation.get("state")
        resource_catalog.attach(report)
        asset_flow.attach(report, flow)
    return report


def main(inputs: Path, output: Path):
    messages = sys.stdout
    sys.stdout = sys.stderr  # Keep library diagnostics off the progress channel.
    lower_priority()

    def watch_owner():
        # The app holds the pipe open. A forced app quit should end even a silent scan.
        os.read(sys.stdin.fileno(), 1)
        os._exit(1)

    threading.Thread(target=watch_owner, daemon=True, name="rom-scan-owner").start()

    def emit(kind, value):
        print(json.dumps({kind: value}), file=messages, flush=True)

    phase, last = None, 0.0

    def progress(value):
        nonlocal phase, last
        now = time.monotonic()
        if value.get("phase") != phase or value.get("done") == value.get("total") or now - last >= .1:
            emit("progress", value)
            phase, last = value.get("phase"), now

    try:
        report = scan(json.loads(inputs.read_bytes()), lambda line: emit("log", line), progress)
        with output.open("w", encoding="utf-8") as stream:
            json.dump(report, stream)
    except (NetworkError, OSError, ValueError) as error:
        emit("error", str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
