# -*- coding: utf-8 -*-
"""Elastic Beanstalk 배포 — vringon-cost 백엔드.

먼저:  aws login   (ap-northeast-2)
       python deploy/eb_bundle.py

    python deploy/eb_deploy.py --create              처음 한 번: 앱·환경 생성(번들 포함)
    python deploy/eb_deploy.py                       평소: 새 번들 올리고 환경 갱신
    python deploy/eb_deploy.py --https --domain cost.rebuilder.ai
                                                     인증서 붙이고 DNS 연결
    python deploy/eb_deploy.py --verify https://cost.rebuilder.ai

생성 키·공급자 주소는 로컬에서 읽어 환경 속성으로만 넘긴다. 화면에 찍지 않고
어디에도 저장하지 않는다.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP, ENV, REGION = "vringon-cost", "vringon-cost-prod", "ap-northeast-2"
ZIP = ROOT / "deploy" / "eb-bundle.zip"
REF_ENV = "vringon-cad-prod"  # 역할·VPC 설정을 빌려오는 기존 환경


def aws(*args, parse=True):
    r = subprocess.run(["aws", *args, "--region", REGION, "--output", "json"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(f"aws {' '.join(args[:3])}: {r.stderr.strip()[:400]}")
    return json.loads(r.stdout) if parse and r.stdout.strip() else None


def secrets():
    """생성 키와 공급자 주소를 로컬 관례 위치에서 읽는다."""
    out = {}
    cmd = ROOT.parent / "scripts" / "run_backend.cmd"
    if cmd.exists():
        m = re.search(r"[A-Z_]*API_KEY\s*=\s*(\S+)", cmd.read_text(encoding="utf-8"))
        if m:
            out["MESH_API_KEY"] = m.group(1)
    prov = ROOT / ".provider.json"
    if prov.exists():
        out["MESH_API_BASE"] = json.loads(prov.read_text(encoding="utf-8"))["base"]
    missing = [k for k in ("MESH_API_KEY", "MESH_API_BASE") if k not in out]
    if missing:
        sys.exit(f"중단: 로컬에서 못 읽음 {missing}")
    return out


def opt_file(pairs):
    items = [{"Namespace": ns, "OptionName": k, "Value": v} for ns, k, v in pairs]
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                    encoding="utf-8")
    json.dump(items, f)
    f.close()
    return f.name


def upload():
    bucket = aws("elasticbeanstalk", "create-storage-location")["S3Bucket"]
    label = "v" + time.strftime("%Y%m%d%H%M%S")
    key = f"{APP}/{label}.zip"
    print(f"번들 {ZIP.stat().st_size/1e6:.1f} MB → s3://{bucket}/{key}")
    subprocess.run(["aws", "s3", "cp", str(ZIP), f"s3://{bucket}/{key}",
                    "--only-show-errors", "--region", REGION], check=True)
    aws("elasticbeanstalk", "create-application-version",
        "--application-name", APP, "--version-label", label,
        "--source-bundle", f"S3Bucket={bucket},S3Key={key}",
        "--description", f"deploy {label}", parse=False)
    print(f"버전 {label} 등록")
    return label


def wait_ready(label=None):
    t0 = time.time()
    while True:
        time.sleep(15)
        e = aws("elasticbeanstalk", "describe-environments",
                "--environment-names", ENV)["Environments"][0]
        print(f"  {int(time.time()-t0)}s  {e['Status']} · {e['Health']}"
              f" · {e.get('VersionLabel')}")
        if e["Status"] == "Ready" and (label is None or e["VersionLabel"] == label):
            return e
        if time.time() - t0 > 25 * 60:
            sys.exit("25분이 지나도 Ready 가 아닙니다")


def ref_option(namespace, name):
    cfg = aws("elasticbeanstalk", "describe-configuration-settings",
              "--application-name", "Vringon-CAD",
              "--environment-name", REF_ENV)["ConfigurationSettings"][0]
    for o in cfg["OptionSettings"]:
        if o["Namespace"] == namespace and o["OptionName"] == name:
            return o.get("Value")
    return None


def create():
    who = aws("sts", "get-caller-identity")
    print(f"계정 {who['Account']} · {who['Arn'].split('/')[-1]}")

    apps = aws("elasticbeanstalk", "describe-applications",
               "--application-names", APP)["Applications"]
    if not apps:
        aws("elasticbeanstalk", "create-application", "--application-name", APP,
            "--description", "신발 Design-to-Should-Cost 백엔드", parse=False)
        print(f"애플리케이션 {APP} 생성")

    stacks = aws("elasticbeanstalk", "list-available-solution-stacks")[
        "SolutionStacks"]
    py = [s for s in stacks if "Amazon Linux 2023" in s and "Python 3.13" in s]
    py = py or [s for s in stacks if "Amazon Linux 2023" in s and "Python" in s]
    if not py:
        sys.exit("Python 솔루션 스택을 못 찾음")
    stack = py[0]
    print(f"스택: {stack}")

    profile = ref_option("aws:autoscaling:launchconfiguration",
                         "IamInstanceProfile") or "aws-elasticbeanstalk-ec2-role"
    service = ref_option("aws:elasticbeanstalk:environment", "ServiceRole") or \
        "aws-elasticbeanstalk-service-role"
    print(f"역할: instance={profile} service={service.split('/')[-1]}")

    label = upload()
    sec = secrets()
    pairs = [
        ("aws:autoscaling:launchconfiguration", "IamInstanceProfile", profile),
        ("aws:autoscaling:launchconfiguration", "InstanceType", "t3.small"),
        ("aws:autoscaling:asg", "MinSize", "1"),
        ("aws:autoscaling:asg", "MaxSize", "1"),
        ("aws:elasticbeanstalk:environment", "ServiceRole", service),
        ("aws:elasticbeanstalk:environment", "EnvironmentType", "LoadBalanced"),
        ("aws:elasticbeanstalk:environment", "LoadBalancerType", "application"),
        ("aws:elasticbeanstalk:environment:proxy", "ProxyServer", "nginx"),
        ("aws:elasticbeanstalk:application", "Application Healthcheck URL",
         "/api/catalog"),
    ] + [("aws:elasticbeanstalk:application:environment", k, v)
         for k, v in sec.items()]
    f = opt_file(pairs)
    try:
        aws("elasticbeanstalk", "create-environment",
            "--application-name", APP, "--environment-name", ENV,
            "--solution-stack-name", stack, "--version-label", label,
            "--option-settings", f"file://{f}", parse=False)
    finally:
        Path(f).unlink()
    print(f"환경 {ENV} 생성 요청 (환경변수 {len(sec)}개 포함)")
    e = wait_ready(label)
    print(f"끝: http://{e['CNAME']}")


def deploy():
    label = upload()
    aws("elasticbeanstalk", "update-environment", "--application-name", APP,
        "--environment-name", ENV, "--version-label", label, parse=False)
    print("환경 갱신 요청됨")
    e = wait_ready(label)
    verify(f"http://{e['CNAME']}")


def https(domain):
    certs = aws("acm", "list-certificates",
                "--certificate-statuses", "ISSUED")["CertificateSummaryList"]

    def covers(c):
        names = {c["DomainName"], *c.get("SubjectAlternativeNameSummaries", [])}
        wild = "*." + domain.split(".", 1)[1]
        return domain in names or wild in names
    hit = [c for c in certs if covers(c)]
    if not hit:
        sys.exit(f"{domain} 을 덮는 발급된 인증서가 없다. "
                 f"ACM 에서 요청·검증 후 다시 실행 (있는 것: "
                 f"{[c['DomainName'] for c in certs]})")
    arn = hit[0]["CertificateArn"]
    print(f"인증서: {hit[0]['DomainName']}")

    f = opt_file([
        ("aws:elbv2:listener:443", "ListenerEnabled", "true"),
        ("aws:elbv2:listener:443", "Protocol", "HTTPS"),
        ("aws:elbv2:listener:443", "SSLCertificateArns", arn),
    ])
    try:
        aws("elasticbeanstalk", "update-environment", "--application-name", APP,
            "--environment-name", ENV, "--option-settings", f"file://{f}",
            parse=False)
    finally:
        Path(f).unlink()
    print("443 리스너 요청됨")
    e = wait_ready()

    zone_name = ".".join(domain.split(".")[-2:]) + "."
    zones = aws("route53", "list-hosted-zones")["HostedZones"]
    zone = next((z for z in zones if z["Name"] == zone_name and
                 not z["Config"]["PrivateZone"]), None)
    if not zone:
        sys.exit(f"호스티드 존 {zone_name} 이 이 계정에 없다. DNS 는 수동으로: "
                 f"CNAME {domain} → {e['CNAME']}")
    ch = {"Changes": [{"Action": "UPSERT", "ResourceRecordSet": {
        "Name": domain, "Type": "CNAME", "TTL": 300,
        "ResourceRecords": [{"Value": e["CNAME"]}]}}]}
    tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8")
    json.dump(ch, tf)
    tf.close()
    try:
        aws("route53", "change-resource-record-sets",
            "--hosted-zone-id", zone["Id"], "--change-batch",
            f"file://{tf.name}")
    finally:
        Path(tf.name).unlink()
    print(f"DNS: {domain} → {e['CNAME']}")
    print(f"끝: https://{domain}  (전파 몇 분)")


def verify(base):
    base = base.rstrip("/")
    checks = [("/api/catalog", 200), ("/api/examples", 200),
              ("/api/projects", 200), ("/", 200),
              ("/api/project/DEMO-RUN-001/cost", 200),
              ("/api/project/DEMO-RUN-001/model.glb", 200)]
    fails = 0
    for p, want in checks:
        try:
            req = urllib.request.Request(base + p, method="GET")
            with urllib.request.urlopen(req, timeout=30) as r:
                got = r.status
        except Exception as ex:
            got = getattr(ex, "code", str(ex)[:60])
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'OK ' if ok else '?? '} {p} → {got} (기대 {want})")
    if fails:
        sys.exit(f"검증 실패 {fails}건")
    print("검증 통과")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--https", action="store_true")
    ap.add_argument("--domain", default="cost.rebuilder.ai")
    ap.add_argument("--verify", nargs="?", const="", default=None)
    a = ap.parse_args()
    if a.create:
        create()
    elif a.https:
        https(a.domain)
    elif a.verify is not None:
        url = a.verify
        if not url:
            e = aws("elasticbeanstalk", "describe-environments",
                    "--environment-names", ENV)["Environments"][0]
            url = f"http://{e['CNAME']}"
        verify(url)
    else:
        deploy()
