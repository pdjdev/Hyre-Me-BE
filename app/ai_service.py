import os
import json
import mimetypes
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# 클라이언트 초기화
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

PORTFOLIO_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["experiences", "certifications", "languages"],
    properties={
        "experiences": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["category", "title"],
                properties={
                    "category": types.Schema(type=types.Type.STRING),
                    "title": types.Schema(type=types.Type.STRING),
                    "organization": types.Schema(type=types.Type.STRING),
                    "period_text": types.Schema(type=types.Type.STRING),
                    "role": types.Schema(type=types.Type.STRING),
                    "tech_stack": types.Schema(type=types.Type.STRING),
                    "description": types.Schema(type=types.Type.STRING),
                    "achievement": types.Schema(type=types.Type.STRING),
                    "learned": types.Schema(type=types.Type.STRING),
                    "related_skills": types.Schema(type=types.Type.STRING),
                },
            ),
        ),
        "certifications": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["name"],
                properties={
                    "name": types.Schema(type=types.Type.STRING),
                    "issuer": types.Schema(type=types.Type.STRING),
                    "acquired_date": types.Schema(type=types.Type.STRING),
                    "description": types.Schema(type=types.Type.STRING),
                },
            ),
        ),
        "languages": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["test_name"],
                properties={
                    "test_name": types.Schema(type=types.Type.STRING),
                    "score": types.Schema(type=types.Type.STRING),
                    "grade": types.Schema(type=types.Type.STRING),
                    "acquired_date": types.Schema(type=types.Type.STRING),
                    "description": types.Schema(type=types.Type.STRING),
                },
            ),
        ),
    },
)

PORTFOLIO_SYSTEM_INSTRUCTION = """
# 지시 사항

- 첨부된 이력서, resume, 포트폴리오 파일에서 경험, 자격증, 어학 성적을 구조화된 JSON으로 추출한다.
- 출력은 반드시 JSON만 반환하고, 설명 문장, 마크다운, 코드 블록은 포함하지 않는다.
- 응답 내용은 한국어 기준으로 작성하되, 시험명, 기술명, 기관명처럼 원문 표기가 자연스러운 값은 원문을 유지할 수 있다.
- 경험, 자격증, 어학 성적을 찾지 못하면 해당 배열은 빈 배열([])로 반환한다.
- 추측으로 채우지 말고 문서에서 확인되는 정보만 사용한다.

## 출력 의미

### experiences
- 한 개의 경험 항목을 하나의 객체로 표현한다.
- category: "프로젝트", "인턴", "경력", "동아리" 중 하나로 분류한다.
- title: 경험 제목으로, 필수 값이다.
- organization: 소속 기관명이나 팀명이다. 문서에 없으면 null로 둔다.
- period_text: 기간을 사람이 읽을 수 있는 문자열로 적는다. 예: 2023.01 ~ 2023.06.
- role: 맡은 역할이나 직책이다.
- tech_stack: 사용한 기술, 도구, 언어, 프레임워크를 적는다.
- description: 수행 내용의 핵심 요약이다.
- achievement: 성과, 수치, 개선 결과를 요약한다.
- learned: 경험을 통해 배운 점을 적는다.
- related_skills: 이 경험으로 드러난 역량 키워드를 적는다.

### certifications
- 한 개의 자격증을 하나의 객체로 표현한다.
- name: 자격증 이름으로, 필수 값이다.
- issuer: 발급 기관명이다.
- acquired_date: 취득일을 YYYY-MM-DD 형식으로 적는다. 정확한 날짜를 모르겠으면 null로 둔다.
- description: 자격증에 대한 보충 설명이다. 없으면 null로 둔다.

### languages
- 한 개의 어학 시험 또는 언어 성적을 하나의 객체로 표현한다.
- test_name: 시험명으로, 필수 값이다. 예: TOEIC, TOEFL, OPIC.
- score: 점수나 정량 결과를 적는다. 예: 850.
- grade: 등급이나 레벨을 적는다. 예: AL, IH.
- acquired_date: 취득일을 YYYY-MM-DD 형식으로 적는다. 정확한 날짜를 모르겠으면 null로 둔다.
- description: 성적에 대한 보충 설명이다. 없으면 null로 둔다.

## 날짜 규칙
- 날짜는 가능한 경우 YYYY-MM-DD 형식 문자열로 반환한다.
- 연도만 알면 YYYY, 월까지 알면 YYYY-MM 형식도 허용한다.
- 아예 알 수 없으면 null로 둔다.

## 추가 규칙
- 각 배열의 객체는 문서에 실제로 존재하는 항목만 넣는다.
- 복수 항목이 있으면 중복 없이 각각 분리해서 반환한다.
""".strip()


def _empty_portfolio_result() -> dict:
    return {"experiences": [], "certifications": [], "languages": []}


def _guess_mime_type(file_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type:
        return mime_type

    extension = os.path.splitext(file_path)[1].lower()
    fallback_mime_types = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".txt": "text/plain",
    }
    return fallback_mime_types.get(extension, "application/octet-stream")

def extract_portfolio_data_with_ai(file_path: str) -> dict:
    """
    저장된 이력서 파일을 구글 Gemini API에 전송하여 JSON 형태로 포트폴리오 데이터를 추출
    """
    uploaded_file = None
    try:
        # 1. 파일을 Gemini 서버에 업로드
        print(f"Uploading {file_path} to Gemini...")
        mime_type = _guess_mime_type(file_path)
        with open(file_path, "rb") as file_obj:
            uploaded_file = client.files.upload(
                file=file_obj,
                config=types.UploadFileConfig(mime_type=mime_type),
            )

        # 환경변수에서 읽어온 모델 확인용 출력
        print(f"Using AI Model: {GEMINI_MODEL_NAME}")

        # 2. 시스템 지시와 구조화 출력 스키마를 함께 적용
        response = client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=[uploaded_file],
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=PORTFOLIO_RESPONSE_SCHEMA,
                system_instruction=[
                    types.Part.from_text(text=PORTFOLIO_SYSTEM_INSTRUCTION),
                ],
            ),
        )
        
        # 3. 응답받은 텍스트를 파이썬 딕셔너리로 변환하여 반환
        result_dict = json.loads(response.text)
        return result_dict

    except Exception as e:
        print(f"AI Extraction Error: {e}")
        # 에러 발생 시 빈 데이터 반환
        return _empty_portfolio_result()

    finally:
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception as delete_error:
                print(f"Failed to delete uploaded file: {delete_error}")