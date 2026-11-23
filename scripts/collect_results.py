"""Copy raw benchmark output into results/, dropping the host name and local
paths that Google Benchmark records, and add a machine description with the
load average before and after the run and the commit that was measured."""
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


def load_avg(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [float(x) for x in f.read().split()[:3]]
    except OSError:
        return None


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


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
        "git_sha": git_sha(),
        "load_before": load_avg(os.path.join(raw_dir, "load_before.txt")),
        "load_after": load_avg(os.path.join(raw_dir, "load_after.txt")),
    }
    os.makedirs(out_dir, exist_ok=True)
    for name, data in (("bench.json", bench), ("latency.json", latency), ("machine.json", machine)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
            f.write("\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
