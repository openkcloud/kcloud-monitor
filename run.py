import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
import uvicorn

# 프로젝트 루트를 Python path에 추가
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 환경 변수 로드 (app 모듈 가져오기 전에 수행)
load_dotenv()


def main():
    """서비스 실행 메인 함수"""
    parser = argparse.ArgumentParser(
        description="AI Accelerator & Infrastructure Monitoring API Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 기본 실행 (127.0.0.1:8000, 자동 리로드 활성화)
  python run.py

  # 포트 변경
  python run.py --port 8001

  # 외부 접속 허용
  python run.py --host 0.0.0.0 --port 8080

  # 프로덕션 모드 (자동 재시작 비활성화)
  python run.py --no-reload

접속 URL:
  - API 문서 (Swagger): http://localhost:8000/docs
  - API 문서 (ReDoc): http://localhost:8000/redoc
  - 헬스체크: http://localhost:8000/api/v1/system/health
  - 메트릭: http://localhost:8000/api/v1/system/metrics
        """
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="바인딩할 호스트 (기본값: 127.0.0.1)"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="바인딩할 포트 (기본값: 8000)"
    )

    parser.add_argument(
        "--reload",
        action="store_true",
        default=True,
        help="코드 변경 시 자동 재시작 (개발 모드, 기본값: True)"
    )

    parser.add_argument(
        "--no-reload",
        action="store_false",
        dest="reload",
        help="자동 재시작 비활성화 (프로덕션 모드)"
    )

    args = parser.parse_args()

    # 환경변수 로드 확인
    try:
        from app.config import settings
        print(f"✅ 환경변수 로드 완료")
        print(f"   - Prometheus URL: {settings.PROMETHEUS_URL}")
        print(f"   - Default Cluster: {settings.DEFAULT_CLUSTER}")
        if settings.PROMETHEUS_CLUSTERS:
            print(f"   - Multi-cluster mode: Enabled")
    except Exception as e:
        print(f"⚠️  환경변수 로드 실패: {e}")
        print(f"   기본 설정으로 실행합니다.")

    print(f"\n{'='*60}")
    print(f"🚀 AI Accelerator & Infrastructure Monitoring API")
    print(f"{'='*60}")
    print(f"📡 Server: http://{args.host}:{args.port}")
    print(f"📚 API Docs (Swagger): http://{args.host}:{args.port}/docs")
    print(f"📖 API Docs (ReDoc): http://{args.host}:{args.port}/redoc")
    print(f"❤️  Health Check: http://{args.host}:{args.port}/api/v1/system/health")
    print(f"📊 Metrics: http://{args.host}:{args.port}/api/v1/system/metrics")
    print(f"{'='*60}\n")

    # uvicorn 실행
    try:
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload
        )
    except KeyboardInterrupt:
        print("\n\n👋 서버를 종료합니다...")
    except Exception as e:
        print(f"❌ 서버 실행 실패: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
