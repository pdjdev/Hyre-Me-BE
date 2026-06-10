import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# 클라이언트 초기화
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# .env에서 모델 이름 추출 (.env에 없을 경우 기본값으로 'gemini-2.5-flash' 사용)
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

def extract_portfolio_data_with_ai(file_path: str) -> dict:
    """
    저장된 이력서 파일을 구글 Gemini API에 전송하여 JSON 형태로 포트폴리오 데이터를 추출
    """
    try:
        # 1. 파일을 Gemini 서버에 업로드 
        print(f"Uploading {file_path} to Gemini...")
        uploaded_file = client.files.upload(file=file_path)

        # 2. 프롬프트 
        prompt = """
        너는 전문적인 이력서 분석 AI야. 
        첨부된 파일을 분석해서 '경험(프로젝트/경력 등)', '자격증', '어학성적' 정보를 추출해줘.
        
        반드시 아래 JSON 스키마에 정확히 맞춰서 답변해야 해.
        해당하는 정보가 파일에 없다면 빈 배열([])을 반환해.
        날짜(acquired_date)는 반드시 "YYYY-MM-DD" 형식으로 작성하고, 모르면 null로 해줘.

        {
            "experiences": [
                {
                    "category": "프로젝트/인턴/경력/동아리 중 하나",
                    "title": "경험 제목 (필수)",
                    "organization": "소속 기관명 (없으면 null)",
                    "period_text": "기간 (예: 2023.01 ~ 2023.06)",
                    "role": "맡은 역할",
                    "tech_stack": "사용한 기술 (예: Python, React)",
                    "description": "상세 내용 요약",
                    "achievement": "성과 요약",
                    "learned": "배운 점",
                    "related_skills": "관련 역량 키워드"
                }
            ],
            "certifications": [
                {
                    "name": "자격증 이름 (필수)",
                    "issuer": "발급 기관",
                    "acquired_date": "YYYY-MM-DD 형식 (모르면 null)",
                    "description": "설명 (없으면 null)"
                }
            ],
            "languages": [
                {
                    "test_name": "시험명 (필수, 예: TOEIC)",
                    "score": "점수 (예: 850)",
                    "grade": "등급 (예: AL, IH)",
                    "acquired_date": "YYYY-MM-DD 형식 (모르면 null)",
                    "description": "설명 (없으면 null)"
                }
            ]
        }
        """

        # 환경변수에서 읽어온 모델 확인용 출력
        print(f"Using AI Model: {GEMINI_MODEL_NAME}")

        # 3. AI에게 파일과 프롬프트 전송 및 JSON 응답 강제  
        response = client.models.generate_content(
            model=GEMINI_MODEL_NAME,  
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        
        # 4. 응답받은 텍스트를 파이썬 딕셔너리로 변환하여 반환
        result_dict = json.loads(response.text)
        
        # 5. 구글 서버에 올린 임시 파일 삭제 (새 방식)
        client.files.delete(name=uploaded_file.name)
        
        return result_dict

    except Exception as e:
        print(f"AI Extraction Error: {e}")
        # 에러 발생 시 빈 데이터 반환
        return {"experiences": [], "certifications": [], "languages": []}