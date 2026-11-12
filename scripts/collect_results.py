"""Copy raw benchmark output into results/, dropping the host name and local
paths that Google Benchmark records, and add a machine description."""
import json
import os
import platform
import subprocess
import sys


def cpu_model():
    if platform.system() == "Darwin":
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                              capture_output=True, text=True, check=True).stdout.strip()
    with open("/proc/cpuinfo", encoding="utf-8") as f:
        for line in f:
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def main(raw_dir, out_dir):
    with open(os.path.join(raw_dir, "bench.json"), encoding="utf-8") as f:
        bench = json.load(f)
    for key in ("host_name", "executable"):
        bench["context"].pop(key, None)
    with open(os.path.join(raw_dir, "latency.json"), encoding="utf-8") as f:
        latency = json.load(f)
    machine = {
        "cpu": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
    }
    os.makedirs(out_dir, exist_ok=True)
    for name, data in (("bench.json", bench), ("latency.json", latency), ("machine.json", machine)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
            f.write("\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
