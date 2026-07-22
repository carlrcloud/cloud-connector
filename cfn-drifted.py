#!/usr/bin/env python3
"""
List all CloudFormation stacks that have drifted.

Two modes:
1. --check-only (default): reads each stack's LAST KNOWN drift status
   (fast, no API calls to trigger detection).
2. --detect: actively triggers drift detection on every stack first,
   waits for results, then reports (slower, but up-to-date).

Usage:
    python list_drifted_stacks.py
    python list_drifted_stacks.py --detect
    python list_drifted_stacks.py --detect --region us-east-1 --profile myprofile
"""

import argparse
import sys
import time

import boto3


def get_all_stacks(cfn):
    """Return all non-deleted stacks (list_stacks excludes DELETE_COMPLETE by default filter set)."""
    stacks = []
    paginator = cfn.get_paginator("list_stacks")
    statuses = [
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
        "IMPORT_COMPLETE", "IMPORT_ROLLBACK_COMPLETE",
    ]
    for page in paginator.paginate(StackStatusFilter=statuses):
        stacks.extend(page["StackSummaries"])
    return stacks


def check_only(cfn):
    """Read existing DriftInformation without triggering new detection."""
    drifted = []
    for s in get_all_stacks(cfn):
        detail = cfn.describe_stacks(StackName=s["StackName"])["Stacks"][0]
        drift_info = detail.get("DriftInformation", {})
        status = drift_info.get("StackDriftStatus", "UNKNOWN")
        if status == "DRIFTED":
            drifted.append({
                "StackName": s["StackName"],
                "DriftStatus": status,
                "LastCheckTime": drift_info.get("LastCheckTimestamp"),
            })
    return drifted


def detect_and_check(cfn):
    """Trigger drift detection on every stack, poll until done, then report drifted ones."""
    stacks = get_all_stacks(cfn)
    detection_ids = {}

    for s in stacks:
        try:
            resp = cfn.detect_stack_drift(StackName=s["StackName"])
            detection_ids[resp["StackDriftDetectionId"]] = s["StackName"]
        except Exception as e:
            print(f"  [skip] {s['StackName']}: {e}", file=sys.stderr)

    drifted = []
    pending = dict(detection_ids)

    print(f"Triggered drift detection on {len(pending)} stacks, polling...")
    while pending:
        for detection_id, stack_name in list(pending.items()):
            result = cfn.describe_stack_drift_detection_status(
                StackDriftDetectionId=detection_id
            )
            status = result["DetectionStatus"]
            if status in ("DETECTION_COMPLETE", "DETECTION_FAILED"):
                if status == "DETECTION_COMPLETE" and result["StackDriftStatus"] == "DRIFTED":
                    drifted.append({
                        "StackName": stack_name,
                        "DriftStatus": result["StackDriftStatus"],
                        "LastCheckTime": result.get("Timestamp"),
                    })
                elif status == "DETECTION_FAILED":
                    print(f"  [failed] {stack_name}: {result.get('DetectionStatusReason')}", file=sys.stderr)
                del pending[detection_id]
        if pending:
            time.sleep(3)

    return drifted


def main():
    parser = argparse.ArgumentParser(description="List drifted CloudFormation stacks")
    parser.add_argument("--detect", action="store_true",
                         help="Actively trigger drift detection instead of reading last known status")
    parser.add_argument("--region", default=None, help="AWS region")
    parser.add_argument("--profile", default=None, help="AWS named profile")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    cfn = session.client("cloudformation")

    if args.detect:
        drifted = detect_and_check(cfn)
    else:
        print("Reading last known drift status (use --detect to force a fresh check)...")
        drifted = check_only(cfn)

    print()
    if not drifted:
        print("No drifted stacks found.")
        return

    print(f"Drifted stacks ({len(drifted)}):")
    for d in drifted:
        print(f"  - {d['StackName']}  (last checked: {d['LastCheckTime']})")


if __name__ == "__main__":
    main()
