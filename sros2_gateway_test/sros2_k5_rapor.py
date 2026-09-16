#!/usr/bin/env python3
"""Convert the focused SROS2 run logs into a reproducible K5 report."""
from __future__ import annotations
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read(name):
    p = Path('/tmp') / name
    return p.read_text(encoding='utf-8', errors='replace') if p.exists() else ''

def main():
    cases = [
        ('authorized_gateway', 'sros2r_test1_pub.log', 'sros2r_test1_sub.log', True),
        ('valid_identity_rogue', 'sros2r_test2_pub.log', 'sros2r_test2_sub.log', False),
        ('anonymous_rogue', 'sros2r_test3_pub.log', 'sros2r_test3_sub.log', False),
    ]
    rows = []
    for name, pub, sub, expected in cases:
        pub_text, sub_text = read(pub), read(sub)
        rows.append({
            'case': name,
            'expected_publish_permission': expected,
            'publisher_created': 'published:' in pub_text,
            'executor_received': 'RECEIVED:' in sub_text,
            'received_count': sub_text.count('RECEIVED:'),
            'permission_denied': 'not found in allow rule' in pub_text or 'TOPLAM_ALINAN=0' in sub_text,
        })
    policy = ROOT / 'policy.xml'
    report = {
        'schema': 'k5-sros2-focused/v1',
        'domain_id': 99,
        'rmw': 'rmw_fastrtps_cpp',
        'security_strategy': 'Enforce',
        'topic': '/mission/command',
        'policy_sha256': hashlib.sha256(policy.read_bytes()).hexdigest(),
        'cases': rows,
        'pass': all((r['publisher_created'] and r['executor_received']) if r['expected_publish_permission'] else (not r['executor_received']) for r in rows),
        'scope': 'gateway_real -> executor_real command topic only; full ROS graph not Enforce-tested',
    }
    (ROOT / 'k5_sros2_rapor.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['pass'] else 1)

if __name__ == '__main__':
    main()
