"""DNS after `make up`: scripts/dns-clean.sh and scripts/wait-for-url.sh, against stubs.

The incident: after `make down` -> `make up` the Gateway was Programmed but DOMAIN didn't resolve.
(1) The zone still held the previous cluster's A/AAAA alias records and no ownership TXT, which
ExternalDNS's TXT registry won't touch. (2) The alias was health-evaluated, so the authoritative
server answered NOERROR with no addresses while the new targets were unhealthy.

`aws`, `kubectl`, `dig`, and `curl` are stubs on PATH that serve canned JSON from a directory (the
scripts read `--output json` and filter with jq, so the stubs return the real shapes).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLEAN = REPO / "scripts" / "dns-clean.sh"
WAIT = REPO / "scripts" / "wait-for-url.sh"
VERIFY = REPO / "scripts" / "teardown-verify.sh"

DOMAIN = "consoletrack.example.com"
ZONE = "Z0123456789ABCDEFGHIJ"
SUFFIX = "a1b2c3"
ALB = "k8s-consoletrack-0123456789.us-east-1.elb.amazonaws.com"
OLD_ALB = "k8s-consoletrack-9999999999.us-east-1.elb.amazonaws.com"

AWS = r"""#!/usr/bin/env bash
# Stub. Files in $STUB_DIR: records.json (or records.<n>.json, one per list call, the last one
# repeating), zone.json, lbs.json, tgs.json, health-<target group name>.json.
echo "$*" >>"$STUB_DIR/calls"
args=" $* "
case $args in
*" route53 list-resource-record-sets "*)
	n=$(($(cat "$STUB_DIR/list-count" 2>/dev/null || echo 0) + 1)); echo "$n" >"$STUB_DIR/list-count"
	f="$STUB_DIR/records.$n.json"
	if [[ ! -f $f ]]; then f=$(ls "$STUB_DIR"/records.[0-9]*.json 2>/dev/null | sort -V | tail -n1); fi
	cat "${f:-$STUB_DIR/records.json}" ;;
*" route53 change-resource-record-sets "*)
	batch=$(sed -E 's/.*--change-batch ([^ ]+( [^-][^ ]*)*) --output.*/\1/' <<<"$*")
	printf '%s' "$batch" >"$STUB_DIR/batch.json"
	jq -c --slurpfile b "$STUB_DIR/batch.json" '.ResourceRecordSets -= [$b[0].Changes[].ResourceRecordSet]' \
		"$STUB_DIR/records.json" >"$STUB_DIR/records.new" && mv "$STUB_DIR/records.new" "$STUB_DIR/records.json" ;;
*" route53 get-hosted-zone "*) cat "$STUB_DIR/zone.json" ;;
*" elbv2 describe-load-balancers "*) cat "$STUB_DIR/lbs.json" 2>/dev/null ;;
*" elbv2 describe-target-groups "*) cat "$STUB_DIR/tgs.json" ;;
*" elbv2 describe-target-health "*)
	tg=$(sed -E 's/.*--target-group-arn ([^ ]+).*/\1/' <<<"$args"); cat "$STUB_DIR/health-${tg##*/}.json" ;;
esac
exit 0
"""
KUBECTL = '#!/usr/bin/env bash\ncat "$STUB_DIR/gateways.json"\n'
# `dig +short @ns DOMAIN A` is the authoritative query; anything else resolves the ALB.
DIG = r"""#!/usr/bin/env bash
echo "dig $*" >>"$STUB_DIR/calls"
if [[ " $* " == *" @"* ]]; then cat "$STUB_DIR/auth-answer" 2>/dev/null; else echo 198.51.100.7; fi
exit 0
"""
CURL = r"""#!/usr/bin/env bash
echo "curl $*" >>"$STUB_DIR/calls"
printf '%s' "${STUB_CURL_CODE-200}"
exit 0
"""


def txt(name: str, owner: str = SUFFIX) -> dict:
    value = f'"heritage=external-dns,external-dns/owner={owner},external-dns/resource=httproute/consoletrack/consoletrack"'
    return {
        "Name": name,
        "Type": "TXT",
        "TTL": 300,
        "ResourceRecords": [{"Value": value}],
    }


def alias(name: str, rtype: str, target: str) -> dict:
    return {
        "Name": name,
        "Type": rtype,
        "AliasTarget": {
            "HostedZoneId": "Z35SXDOTRQ7X7K",
            "DNSName": f"dualstack.{target}.",
            "EvaluateTargetHealth": False,
        },
    }


ZONE_RECORDS = [
    {
        "Name": f"{DOMAIN}.",
        "Type": "NS",
        "TTL": 172800,
        "ResourceRecords": [{"Value": "ns-1.awsdns-01.org."}],
    },
    {
        "Name": f"{DOMAIN}.",
        "Type": "SOA",
        "TTL": 900,
        "ResourceRecords": [
            {"Value": "ns-1.awsdns-01.org. hostmaster 1 7200 900 1209600 86400"}
        ],
    },
    {
        "Name": f"_0a1b2c3d4e5f.{DOMAIN}.",
        "Type": "CNAME",
        "TTL": 300,
        "ResourceRecords": [{"Value": "_9z8y7x.acm-validations.aws."}],
    },
]
EXTERNAL_DNS = [
    alias(f"{DOMAIN}.", "A", ALB),
    alias(f"{DOMAIN}.", "AAAA", ALB),
    txt(f"{DOMAIN}."),
    txt(f"a-{DOMAIN}."),
    txt(f"aaaa-{DOMAIN}."),
]


@pytest.fixture
def stub(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("aws", AWS),
        ("kubectl", KUBECTL),
        ("dig", DIG),
        ("curl", CURL),
    ):
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def run(
    script: Path, stub: Path, *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *args],
        env={
            "PATH": f"{stub / 'bin'}:{os.environ['PATH']}",
            "STUB_DIR": str(stub),
            "AWS_REGION": "us-east-1",
            "DOMAIN": DOMAIN,
            "NAME_SUFFIX": SUFFIX,
            "DNS_ZONE_ID": ZONE,
            **env,
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def set_records(stub: Path, records: list[dict], n: int | None = None) -> None:
    name = "records.json" if n is None else f"records.{n}.json"
    (stub / name).write_text(json.dumps({"ResourceRecordSets": records}))


def remaining(stub: Path) -> list[tuple[str, str]]:
    data = json.loads((stub / "records.json").read_text())
    return [(r["Name"], r["Type"]) for r in data["ResourceRecordSets"]]


def calls(stub: Path) -> str:
    f = stub / "calls"
    return f.read_text() if f.exists() else ""


# --- dns-clean.sh -------------------------------------------------------------------------


def test_clean_deletes_domains_records_and_only_those(stub: Path) -> None:
    elsewhere = [
        alias(f"www.{DOMAIN}.", "A", ALB),
        alias(f"other.{DOMAIN}.", "AAAA", ALB),
        txt(f"a-www.{DOMAIN}."),
        txt(f"{DOMAIN}.", owner="someone-else"),
        txt(f"a-{DOMAIN}.", owner=SUFFIX + "x"),
        {
            "Name": f"{DOMAIN}.",
            "Type": "MX",
            "TTL": 300,
            "ResourceRecords": [{"Value": "10 mail.example.com"}],
        },
    ]
    # The same-owner TXT is deleted, a different owner's (even one that shares a prefix) is kept.
    set_records(
        stub, [*ZONE_RECORDS, *EXTERNAL_DNS[:2], txt(f"a-{DOMAIN}."), *elsewhere]
    )
    out = run(CLEAN, stub)
    assert out.returncode == 0, out.stderr
    assert sorted(remaining(stub)) == sorted(
        (r["Name"], r["Type"]) for r in [*ZONE_RECORDS, *elsewhere]
    )
    deleted = json.loads((stub / "batch.json").read_text())["Changes"]
    assert {c["Action"] for c in deleted} == {"DELETE"}
    assert sorted(
        (c["ResourceRecordSet"]["Name"], c["ResourceRecordSet"]["Type"])
        for c in deleted
    ) == sorted(
        [
            (f"{DOMAIN}.", "A"),
            (f"{DOMAIN}.", "AAAA"),
            (f"a-{DOMAIN}.", "TXT"),
        ]
    )
    assert f"A {DOMAIN}" in out.stdout and "deleted 3 record(s)" in out.stdout


def test_clean_removes_stale_records_that_have_no_ownership(stub: Path) -> None:
    """The incident's state: A and AAAA from the previous cluster, no TXT."""
    stale = [alias(f"{DOMAIN}.", "A", OLD_ALB), alias(f"{DOMAIN}.", "AAAA", OLD_ALB)]
    set_records(stub, [*ZONE_RECORDS, *stale])
    out = run(CLEAN, stub)
    assert out.returncode == 0, out.stderr
    assert sorted(remaining(stub)) == sorted(
        (r["Name"], r["Type"]) for r in ZONE_RECORDS
    )
    # No ownership TXT, so it doesn't wait for ExternalDNS.
    assert "waiting" not in out.stdout


def test_clean_never_touches_ns_soa_or_the_acm_validation_cname(stub: Path) -> None:
    set_records(stub, ZONE_RECORDS)
    out = run(CLEAN, stub)
    assert out.returncode == 0
    assert "change-resource-record-sets" not in calls(stub)
    assert "no A, AAAA, or ownership TXT" in out.stdout


def test_clean_waits_for_externaldns_to_delete_its_own(stub: Path) -> None:
    set_records(stub, [*ZONE_RECORDS, *EXTERNAL_DNS], 1)
    set_records(stub, [*ZONE_RECORDS, *EXTERNAL_DNS], 2)
    set_records(stub, ZONE_RECORDS, 3)
    out = run(CLEAN, stub, DNS_CLEAN_WAIT="60", DNS_CLEAN_INTERVAL="0")
    assert out.returncode == 0, out.stderr
    assert out.stdout.count("waiting for ExternalDNS") == 2
    assert "change-resource-record-sets" not in calls(stub)


def test_clean_deletes_the_rest_when_the_wait_runs_out(stub: Path) -> None:
    set_records(stub, [*ZONE_RECORDS, *EXTERNAL_DNS])
    out = run(CLEAN, stub, DNS_CLEAN_WAIT="1", DNS_CLEAN_INTERVAL="1")
    assert out.returncode == 0, out.stderr
    assert "ExternalDNS did not" in out.stdout
    assert sorted(remaining(stub)) == sorted(
        (r["Name"], r["Type"]) for r in ZONE_RECORDS
    )


def test_list_prints_what_clean_would_delete_and_fails(stub: Path) -> None:
    set_records(stub, [*ZONE_RECORDS, *EXTERNAL_DNS])
    out = run(CLEAN, stub, "list")
    assert out.returncode == 1
    assert f"A {DOMAIN}" in out.stdout and f"TXT a-{DOMAIN}" in out.stdout
    assert "change-resource-record-sets" not in calls(stub)
    set_records(stub, ZONE_RECORDS)
    assert run(CLEAN, stub, "list").returncode == 0


def test_clean_needs_a_zone_id_and_skips_without_a_domain(stub: Path) -> None:
    set_records(stub, ZONE_RECORDS)
    out = run(CLEAN, stub, DNS_ZONE_ID="")
    assert out.returncode == 1 and "zone ID" in out.stderr
    out = run(CLEAN, stub, DNS_ZONE_ID="No outputs found")
    assert out.returncode == 1
    out = run(CLEAN, stub, DOMAIN="")
    assert out.returncode == 0 and "nothing to clean" in out.stdout


def test_clean_masks_account_ids(stub: Path) -> None:
    stale = alias(f"{DOMAIN}.", "A", OLD_ALB)
    stale["Name"] = f"{DOMAIN}."
    set_records(stub, [stale])
    out = run(CLEAN, stub)
    assert "123456789012" not in out.stdout


# --- wait-for-url.sh ----------------------------------------------------------------------


def healthy_cluster(stub: Path, *, a_target: str = ALB) -> None:
    """Everything the five steps look at, in the passing state."""
    (stub / "gateways.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "status": {
                            "conditions": [{"type": "Programmed", "status": "True"}],
                            "addresses": [{"type": "Hostname", "value": ALB}],
                        }
                    }
                ]
            }
        )
    )
    set_records(
        stub,
        [
            *ZONE_RECORDS,
            alias(f"{DOMAIN}.", "A", a_target),
            txt(f"{DOMAIN}."),
        ],
    )
    (stub / "zone.json").write_text(
        json.dumps({"DelegationSet": {"NameServers": ["ns-1.awsdns-01.org"]}})
    )
    arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/k8s/abc"
    (stub / "lbs.json").write_text(
        json.dumps({"LoadBalancers": [{"DNSName": ALB, "LoadBalancerArn": arn}]})
    )
    tg = (
        "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/k8s-front/0123"
    )
    (stub / "tgs.json").write_text(
        json.dumps({"TargetGroups": [{"TargetGroupArn": tg}]})
    )
    set_health(stub, "0123", [("10.0.1.5", "healthy", None)])
    (stub / "auth-answer").write_text("203.0.113.10\n203.0.113.11\n")


def set_health(stub: Path, tg: str, targets: list[tuple[str, str, str | None]]) -> None:
    descriptions = [
        {
            "Target": {"Id": i},
            "TargetHealth": {"State": s, **({"Reason": r} if r else {})},
        }
        for i, s, r in targets
    ]
    (stub / f"health-{tg}.json").write_text(
        json.dumps({"TargetHealthDescriptions": descriptions})
    )


def wait(stub: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return run(
        WAIT,
        stub,
        WAIT_URL_INTERVAL="0",
        WAIT_URL_TIMEOUT=env.pop("WAIT_URL_TIMEOUT", "1"),
        **env,
    )


def test_wait_succeeds_and_prints_the_cache_note(stub: Path) -> None:
    healthy_cluster(stub)
    out = wait(stub)
    assert out.returncode == 0, out.stderr
    assert f"https://{DOMAIN} is up" in out.stdout
    assert "15 minutes" in out.stdout
    assert "dscacheutil" in out.stdout and "chrome://net-internals/#dns" in out.stdout
    # It tested the ALB's address with DOMAIN's name, and asked the zone's own name server.
    log = calls(stub)
    assert f"--resolve {DOMAIN}:443:198.51.100.7" in log
    assert f"dig +short +time=3 +tries=1 @ns-1.awsdns-01.org {DOMAIN} A" in log
    assert "123456789012" not in out.stdout + out.stderr


def test_wait_fails_with_no_programmed_gateway(stub: Path) -> None:
    healthy_cluster(stub)
    (stub / "gateways.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "status": {
                            "conditions": [{"type": "Programmed", "status": "False"}]
                        }
                    }
                ]
            }
        )
    )
    out = wait(stub)
    assert out.returncode == 1
    assert "step 1. Gateway" in out.stderr


def test_wait_reports_a_stale_record_at_once(stub: Path) -> None:
    healthy_cluster(stub)
    set_records(stub, [*ZONE_RECORDS, alias(f"{DOMAIN}.", "A", OLD_ALB)])
    out = wait(stub, WAIT_URL_TIMEOUT="300")
    assert out.returncode == 1
    assert "stale record from a previous cluster" in out.stderr
    assert "make dns-clean" in out.stderr


def test_wait_gives_an_owned_record_time_to_update(stub: Path) -> None:
    healthy_cluster(stub)
    set_records(
        stub, [*ZONE_RECORDS, alias(f"{DOMAIN}.", "A", OLD_ALB), txt(f"{DOMAIN}.")], 1
    )
    set_records(
        stub, [*ZONE_RECORDS, alias(f"{DOMAIN}.", "A", ALB), txt(f"{DOMAIN}.")], 2
    )
    out = wait(stub, WAIT_URL_TIMEOUT="30")
    assert out.returncode == 0, out.stderr
    assert "2. DNS record" in out.stdout and "ExternalDNS owns it" in out.stdout


def test_wait_times_out_when_there_is_no_a_record(stub: Path) -> None:
    healthy_cluster(stub)
    set_records(stub, ZONE_RECORDS)
    out = wait(stub)
    assert out.returncode == 1
    assert "step 2. DNS record" in out.stderr and "no A record" in out.stderr


def test_wait_lists_each_target_when_none_is_healthy(stub: Path) -> None:
    healthy_cluster(stub)
    set_health(
        stub,
        "0123",
        [
            ("10.0.1.5", "unhealthy", "Target.FailedHealthChecks"),
            ("10.0.2.9", "initial", "Elb.RegistrationInProgress"),
        ],
    )
    out = wait(stub)
    assert out.returncode == 1
    assert "step 3. ALB targets" in out.stderr
    assert "10.0.1.5 unhealthy (Target.FailedHealthChecks)" in out.stderr
    assert "10.0.2.9 initial (Elb.RegistrationInProgress)" in out.stderr


def test_wait_fails_for_a_target_group_with_no_targets(stub: Path) -> None:
    healthy_cluster(stub)
    set_health(stub, "0123", [])
    out = wait(stub)
    assert out.returncode == 1 and "no targets registered" in out.stderr


def test_wait_fails_on_an_empty_authoritative_answer(stub: Path) -> None:
    """NOERROR with no answers: what a health-evaluated alias returned while targets were unhealthy."""
    healthy_cluster(stub)
    (stub / "auth-answer").write_text("")
    out = wait(stub)
    assert out.returncode == 1
    assert "step 4. Authoritative DNS" in out.stderr and "no addresses" in out.stderr


def test_wait_fails_when_curl_does_not_get_2xx_or_3xx(stub: Path) -> None:
    healthy_cluster(stub)
    out = wait(stub, STUB_CURL_CODE="503")
    assert out.returncode == 1
    assert "step 5. HTTPS" in out.stderr and "503" in out.stderr
    assert wait(stub, STUB_CURL_CODE="000").returncode == 1
    assert wait(stub, STUB_CURL_CODE="302").returncode == 0


def test_wait_polls_until_the_deadline(stub: Path) -> None:
    healthy_cluster(stub)
    out = run(
        WAIT, stub, STUB_CURL_CODE="503", WAIT_URL_INTERVAL="1", WAIT_URL_TIMEOUT="2"
    )
    assert out.returncode == 1
    assert "gave up after 2s" in out.stderr
    assert calls(stub).count("--resolve") >= 2


# --- teardown-verify.sh ---------------------------------------------------------------------


def verify(stub: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return run(VERIFY, stub, "verify", **env)


def test_verify_clean_lists_dns_records_left_for_the_domain(stub: Path) -> None:
    # Stub aws answers every EC2 and ELB call with nothing, so only DNS can be left.
    set_records(stub, [*ZONE_RECORDS, *EXTERNAL_DNS])
    out = verify(stub)
    assert out.returncode == 1
    assert f"dns-record A {DOMAIN}" in out.stderr
    assert f"dns-record TXT a-{DOMAIN}" in out.stderr
    assert "make dns-clean" in out.stderr
    set_records(stub, ZONE_RECORDS)
    assert verify(stub).returncode == 0
    # Without a zone ID there is nothing to look at, and it says so.
    out = verify(stub, DNS_ZONE_ID="")
    assert out.returncode == 0 and "DNS records not checked" in out.stdout


# --- print-logins.sh ------------------------------------------------------------------------

LOGINS = REPO / "scripts" / "print-logins.sh"


def logins(
    stub: Path, banner: str | None, **env: str
) -> subprocess.CompletedProcess[str]:
    # kubectl stub prints "c2VjcmV0" (base64 of "secret") unless STUB_NO_GRAFANA is set.
    k = stub / "bin" / "kubectl"
    k.write_text(
        '#!/usr/bin/env bash\n[[ -z "${STUB_NO_GRAFANA-}" ]] && printf c2VjcmV0 || exit 1\n'
    )
    if banner is not None:
        (stub / "banner").write_text(banner)
        env["LOGIN_BANNER_FILE"] = str(stub / "banner")
    return run(LOGINS, stub, **env)


def test_logins_prints_the_generated_admin_password_and_grafana(stub: Path) -> None:
    out = logins(
        stub, "  username:  admin\n      password:  hunter2hunter2\n", EXPOSURE="alb"
    )
    assert out.returncode == 0, out.stderr
    assert f"https://{DOMAIN}" in out.stdout
    assert "password:  hunter2hunter2" in out.stdout
    assert "password:  secret" in out.stdout and "port-forward" in out.stdout


def test_logins_on_a_rerun_says_the_admin_password_is_not_shown_again(
    stub: Path,
) -> None:
    out = logins(stub, None, EXPOSURE="port-forward", STUB_NO_GRAFANA="1")
    assert out.returncode == 0
    assert "not shown again" in out.stdout and "make open" in out.stdout
    assert "could not read the kube-prometheus-stack-grafana Secret" in out.stdout
