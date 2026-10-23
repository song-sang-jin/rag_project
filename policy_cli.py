"""python policy_cli.py collect | index | status"""
import argparse
import json
from youth_rag.api import collect_all
from youth_rag.config import DATA
from youth_rag.data import load_policies, validate, write_json
from youth_rag.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["collect", "index", "status"])
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "collect":
            _, report = collect_all(print, refresh=args.refresh)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "index":
            policies = load_policies()
            if not policies:
                raise ValueError("먼저 collect를 실행하세요.")
            store = Store()
            store.index_policies(policies, print)
            print(f"항목 색인 {store.meta.count()}건 / 외부 문서 청크 {store.docs.count()}건")
        else:
            report = validate(load_policies())
            store = Store()
            report.update({"metadata_chunks": store.meta.count(), "document_chunks": store.docs.count()})
            print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception as exc:
        # 인증정보가 포함될 수 있는 외부 예외를 콘솔에 출력하지 않는다.
        print(f"작업 실패 ({type(exc).__name__}). 설정·네트워크·API 승인 상태를 확인하세요.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
