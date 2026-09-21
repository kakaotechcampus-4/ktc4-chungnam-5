"""서비스 시간대.

DB 는 UTC(timestamptz)로 저장하고, "하루" 경계와 응답 표기는 KST 로 한다.
"""

from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
