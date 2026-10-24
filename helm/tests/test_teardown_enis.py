"""Leftover network interfaces after a node group is destroyed.

scripts/delete-leftover-enis.sh (`make clean-enis`, and `make down` between the node group and the
rest of the root) and `scripts/teardown-verify.sh verify` (`make verify-clean`), against a stub
`aws` that serves a list of ENIs from a file. The bug: the VPC CNI leaves `available`
"aws-K8S-i-<instance>" ENIs, which block deleting the node security group and the subnets, and
`make verify-clean` didn't list them because they carry no project tag. And `make down` waited
for the control plane's "Amazon EKS <cluster>" ENIs while the cluster was still running, so they
were never released and the wait always timed out.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLEAN = REPO / "scripts" / "delete-leftover-enis.sh"
VERIFY = REPO / "scripts" / "teardown-verify.sh"

AWS = r"""#!/usr/bin/env bash
# Stub: ENIs live in $STUB_DIR/enis as "<id>\t<status>\t<description>" rows; the cluster's status
# is $STUB_CLUSTER (unset: no such cluster).
echo "$*" >>"$STUB_DIR/calls"
args=" $* "
case $args in
*" describe-vpcs "*) echo "${STUB_VPCS-vpc-0abc}" ;;
*" describe-cluster "*)
	if [[ -n "${STUB_CLUSTER_ERROR-}" ]]; then echo "$STUB_CLUSTER_ERROR" >&2; exit 254; fi
	if [[ -z "${STUB_CLUSTER-}" ]]; then
		echo "An error occurred (ResourceNotFoundException) when calling the DescribeCluster operation: No cluster found" >&2
		exit 254
	fi
	echo "$STUB_CLUSTER" ;;
*" describe-network-interfaces "*Name=vpc-id*) cat "$STUB_DIR/enis" ;;
*" delete-network-interface "*)
	id=$(sed -E 's/.*--network-interface-id ([^ ]+).*/\1/' <<<"$args")
	grep -v "^$id	" "$STUB_DIR/enis" >"$STUB_DIR/enis.new"; mv "$STUB_DIR/enis.new" "$STUB_DIR/enis" ;;
esac
exit 0
"""

CNI = "eni-0cni\tavailable\taws-K8S-i-0298a2b4452459853"
NAT = "eni-0nat\tin-use\tInterface for NAT Gateway nat-0123"
EKS = "eni-0eks\tin-use\tAmazon EKS ct-eks-use1-test"
OTHER = "eni-0other\tavailable\tsomething else"


@pytest.fixture
def stub(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(AWS)
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def run(
    script: Path, stub: Path, enis: list[str], *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    (stub / "enis").write_text("".join(f"{row}\n" for row in enis))
    return subprocess.run(
        [str(script), *args],
        env={
            "PATH": f"{stub / 'bin'}:{os.environ['PATH']}",
            "STUB_DIR": str(stub),
            "AWS_REGION": "us-east-1",
            **env,
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def deleted(stub: Path) -> list[str]:
    calls = (stub / "calls").read_text().splitlines()
    return [
        c.split("--network-interface-id ")[1]
        for c in calls
        if "delete-network-interface" in c
    ]


def remaining(stub: Path) -> list[str]:
    return [row.split("\t")[0] for row in (stub / "enis").read_text().splitlines()]


# --- make clean-enis / make down ------------------------------------------------------------


def test_deletes_available_cni_enis_and_leaves_terraforms_own(stub: Path) -> None:
    result = run(CLEAN, stub, [CNI, NAT], CLEAN_ENIS_ASSUME_YES="1")
    assert result.returncode == 0, result.stderr
    assert deleted(stub) == ["eni-0cni"]
    assert remaining(stub) == [
        "eni-0nat"
    ]  # the NAT gateway's ENI goes with its gateway


def test_never_deletes_an_available_eni_that_is_not_the_cnis(stub: Path) -> None:
    result = run(CLEAN, stub, [OTHER], CLEAN_ENIS_ASSUME_YES="1")
    assert result.returncode == 0, result.stderr
    assert deleted(stub) == []


def test_never_deletes_an_in_use_eni(stub: Path) -> None:
    in_use_cni = "eni-0busy\tin-use\taws-K8S-i-0298a2b4452459853"
    result = run(CLEAN, stub, [in_use_cni], CLEAN_ENIS_ASSUME_YES="1")
    assert (
        result.returncode == 0
    )  # not an EKS-managed one, so not this script's to wait for
    assert deleted(stub) == []


def test_eks_managed_enis_fail_when_not_waiting(stub: Path) -> None:
    result = run(
        CLEAN,
        stub,
        [CNI, EKS],
        CLEAN_ENIS_ASSUME_YES="1",
        CLEAN_ENIS_WAIT="0",
        STUB_CLUSTER="DELETING",
    )
    assert result.returncode == 1
    assert "eni-0eks" in result.stderr
    assert deleted(stub) == ["eni-0cni"]


def test_a_running_clusters_enis_are_left_alone_without_waiting(stub: Path) -> None:
    # make down runs this before Terraform deletes the cluster: its control-plane ENIs stay until
    # then, so waiting (CLEAN_ENIS_WAIT=600) would only time out.
    result = run(
        CLEAN,
        stub,
        [CNI, EKS],
        CLEAN_ENIS_ASSUME_YES="1",
        CLEAN_ENIS_WAIT="600",
        STUB_CLUSTER="ACTIVE",
    )
    assert result.returncode == 0, result.stderr
    assert "running cluster" in result.stdout
    assert "waiting" not in result.stdout
    assert deleted(stub) == ["eni-0cni"]
    assert remaining(stub) == ["eni-0eks"]


def test_deletes_available_control_plane_enis_once_the_cluster_is_gone(
    stub: Path,
) -> None:
    orphan = "eni-0eks\tavailable\tAmazon EKS ct-eks-use1-test"
    result = run(CLEAN, stub, [orphan], CLEAN_ENIS_ASSUME_YES="1")
    assert result.returncode == 0, result.stderr
    assert deleted(stub) == ["eni-0eks"]


def test_keeps_an_available_control_plane_eni_of_a_running_cluster(
    stub: Path,
) -> None:
    available = "eni-0eks\tavailable\tAmazon EKS ct-eks-use1-test"
    result = run(
        CLEAN, stub, [available], CLEAN_ENIS_ASSUME_YES="1", STUB_CLUSTER="ACTIVE"
    )
    assert result.returncode == 0, result.stderr
    assert deleted(stub) == []


def test_fails_when_it_cannot_tell_whether_the_cluster_exists(stub: Path) -> None:
    result = run(
        CLEAN,
        stub,
        [EKS],
        CLEAN_ENIS_ASSUME_YES="1",
        STUB_CLUSTER_ERROR="An error occurred (AccessDeniedException): arn:aws:iam::123456789012:user/x",
    )
    assert result.returncode == 1
    assert "can't tell" in result.stderr
    assert "123456789012" not in result.stderr
    assert deleted(stub) == []


def test_asks_before_deleting_and_refuses_without_a_terminal(stub: Path) -> None:
    result = run(CLEAN, stub, [CNI])
    assert result.returncode == 1
    assert "interactive terminal" in result.stderr
    assert "eni-0cni" in result.stdout
    assert deleted(stub) == []


def test_no_project_vpc_means_nothing_to_clean(stub: Path) -> None:
    result = run(CLEAN, stub, [CNI], STUB_VPCS="", CLEAN_ENIS_ASSUME_YES="1")
    assert result.returncode == 0
    assert "nothing to clean" in result.stdout
    assert deleted(stub) == []


# --- make verify-clean ----------------------------------------------------------------------


def test_verify_lists_untagged_cni_enis_with_their_status_and_a_hint(
    stub: Path,
) -> None:
    result = run(VERIFY, stub, [CNI, NAT], "verify")
    assert result.returncode == 1
    assert "eni eni-0cni (available) aws-K8S-i-0298a2b4452459853" in result.stderr
    assert "make clean-enis" in result.stderr


def test_verify_masks_account_ids(stub: Path) -> None:
    result = run(VERIFY, stub, ["eni-0x\tin-use\towned by 691914216412"], "verify")
    assert result.returncode == 1
    assert "691914216412" not in result.stdout + result.stderr


def test_verify_is_clean_without_a_project_vpc(stub: Path) -> None:
    result = run(VERIFY, stub, [CNI], "verify", STUB_VPCS="")
    assert result.returncode == 0, result.stderr
    assert "no project=consoletrack" in result.stdout
