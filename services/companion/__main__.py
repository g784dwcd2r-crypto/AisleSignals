"""Run with python -m services.companion; no installation or network access by default."""

import argparse
import json
import sys

from .readiness import error_result, preflight, probe_environment


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default diagnostic repeats unknown arguments, possibly a URL.
        self.exit(2, json.dumps({"status": "BLOCKED", "code": "INVALID_ARGUMENTS",
                                "message": "Invalid arguments. Use --help; pass only an environment-variable name for a probe."}) + "\n")


def main(argv=None) -> int:
    parser = PrivateArgumentParser(description="AisleSignals read-only laptop readiness utility. No video capture or monitoring.")
    parser.add_argument("--disk-path", default=".", help="Existing directory whose volume is checked; the path is omitted from reports.")
    parser.add_argument("--probe-env", metavar="ENVIRONMENT_VARIABLE_NAME", help="Opt in to a single private-IP RTSP metadata request using a URL from this variable.")
    parser.add_argument("--authorised", action="store_true", help="Confirm permission to probe this exact existing camera endpoint.")
    args = parser.parse_args(argv)
    try:
        report = preflight(args.disk_path)
        if args.probe_env is not None:
            report["network_probe"] = error_result("READINESS_BLOCKED") if report["status"] == "BLOCKED" else probe_environment(args.probe_env, args.authorised)
        if report["network_probe"]["status"] == "BLOCKED":
            report["status"] = "BLOCKED"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2 if report["status"] == "BLOCKED" else 0
    except Exception:
        print(json.dumps({"status": "BLOCKED", "code": "UNEXPECTED_FAILURE",
                          "message": "The readiness operation failed without exposing device details."}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
