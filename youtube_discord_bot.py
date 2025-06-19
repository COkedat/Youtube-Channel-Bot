import os
import re
import time
import requests
from googleapiclient.discovery import build

# --- 설정 부분 ---
# 사용자 정보로 변경 ㄱ
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY") # 유튜브 API 키
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL") # 디스코드 웹훅 URL
TARGET_CHANNEL_ID = os.environ.get("TARGET_CHANNEL_ID") # 감시할 유튜브 채널 ID

# 체크 간격 (초 단위, 너무 짧으면 API 제한에 걸림)
CHECK_INTERVAL_SECONDS = 600 # 10분

# --- 전역 변수 ---
# 가장 최근에 확인된 영상 ID를 저장할 파일
LAST_VIDEO_ID_FILE = "last_video_id.txt"

def get_last_video_id():
    """파일에서 마지막으로 확인한 영상 ID를 읽음"""
    if not os.path.exists(LAST_VIDEO_ID_FILE):
        return None
    with open(LAST_VIDEO_ID_FILE, "r") as f:
        return f.read().strip()

def save_last_video_id(video_id):
    """새로운 영상 ID를 파일에 저장"""
    with open(LAST_VIDEO_ID_FILE, "w") as f:
        f.write(video_id)

def unshorten_url(url):
    """단축 URL을 원래 URL로 변환"""
    try:
        # allow_redirects=True (기본값)를 사용하여 리디렉션을 따라감
        # 헤더만 요청하여 더 빠르고 효율적임
        response = requests.head(url, allow_redirects=True, timeout=5)
        return response.url
    except requests.RequestException:
        # 오류 발생 시 원래 URL 반환
        return url

def process_description(description):
    """설명 텍스트에서 URL을 찾아 단축을 해제"""
    # 정규 표현식을 사용하여 URL 찾음
    url_pattern = re.compile(r'https?://[^\s/$.?#].[^\s]*')
    urls = url_pattern.findall(description)
    
    processed_description = description
    for url in set(urls): # 중복된 URL은 한 번만 처리
        # 모든 URL에 대해 시도함
        original_url = unshorten_url(url)
        if url != original_url:
            print(f"URL 변환: {url} -> {original_url}")
            processed_description = processed_description.replace(url, f"{original_url} (원 주소: {url})")
            
    return processed_description

def get_latest_video():
    """유튜브 API를 사용하여 채널의 최신 영상을 가져옴"""
    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

        # 채널 ID로 채널의 'uploads' 플레이리스트 ID를 가져옴
        channel_response = youtube.channels().list(
            id=TARGET_CHANNEL_ID,
            part='contentDetails'
        ).execute()
        
        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']

        # 'uploads' 플레이리스트에서 최신 영상을 1개 가져옴
        playlist_response = youtube.playlistItems().list(
            playlistId=uploads_playlist_id,
            part='snippet',
            maxResults=1
        ).execute()

        if not playlist_response.get('items'):
            return None # 채널에 영상이 없는 경우

        latest_video = playlist_response['items'][0]['snippet']
        return latest_video

    except Exception as e:
        print(f"유튜브 API 호출 중 오류 발생: {e}")
        return None

def send_to_discord(video_info):
    """디스코드 웹훅으로 메시지를 보냄"""
    video_id = video_info['resourceId']['videoId']
    video_title = video_info['title']
    video_description = video_info['description']
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    print(f"새 영상 발견: {video_title}")
    
    # 설명의 URL 단축 해제
    processed_description = process_description(video_description)

    # 디스코드 메시지 포맷 (Embed 사용)
    data = {
        "content": f"📢 **{video_info['channelTitle']}** 채널에 새 영상이 업로드되었습니다!",
        "embeds": [
            {
                "title": f"🎬 {video_title}",
                "description": f"{processed_description[:2000]}...", # 설명이 너무 길면 자름
                "url": video_url,
                "color": 16711680, # 빨간색 (YouTube 색)
                "thumbnail": {
                    "url": video_info['thumbnails']['high']['url']
                },
                "footer": {
                    "text": f"게시일: {video_info['publishedAt'].split('T')[0]}"
                }
            }
        ]
    }

    response = requests.post(DISCORD_WEBHOOK_URL, json=data)
    try:
        response.raise_for_status()
        print("디스코드로 알림을 성공적으로 보냈습니다.")
    except requests.exceptions.HTTPError as err:
        print(f"디스코드 전송 실패: {err}")

def main():
    """메인 로직: 주기적으로 새 영상을 체크하고 알림을 보냄"""
    print("유튜브-디스코드 알림 봇을 시작합니다.")
    
    last_known_video_id = get_last_video_id()
    if last_known_video_id:
        print(f"마지막으로 확인된 영상 ID: {last_known_video_id}")
    else:
        # 처음 실행 시, 가장 최신 영상을 ID만 저장하고 알림은 보내지 않음
        print("처음 실행합니다. 기준이 될 최신 영상을 저장합니다.")
        latest_video = get_latest_video()
        if latest_video:
            video_id = latest_video['resourceId']['videoId']
            save_last_video_id(video_id)
            print(f"기준 영상 ID 저장됨: {video_id}")
        return # 초기화 후 종료, 다음 실행부터 정상 작동

    while True:
        latest_video = get_latest_video()

        if latest_video:
            current_video_id = latest_video['resourceId']['videoId']
            
            if current_video_id != last_known_video_id:
                # 새로운 영상이 올라옴
                send_to_discord(latest_video)
                save_last_video_id(current_video_id)
                last_known_video_id = current_video_id
            else:
                # 새로운 영상이 없음
                print(f"새 영상 없음. 마지막 확인 ID: {current_video_id}")
        
        print(f"{CHECK_INTERVAL_SECONDS}초 후에 다시 확인합니다.")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    # 설정값이 비어있는지 확인
    if not all([YOUTUBE_API_KEY, DISCORD_WEBHOOK_URL, TARGET_CHANNEL_ID]) or \
       "YOUR_" in YOUTUBE_API_KEY or "YOUR_" in DISCORD_WEBHOOK_URL or "YOUR_" in TARGET_CHANNEL_ID:
        print("오류: 코드의 설정 부분(YOUTUBE_API_KEY, DISCORD_WEBHOOK_URL, TARGET_CHANNEL_ID)을 올바르게 채워주세요.")
    else:
        # 최초 실행 시 초기화 로직을 위해 main()을 한 번 호출
        main_instance_running = False
        if not os.path.exists(LAST_VIDEO_ID_FILE):
             main() # 초기화 실행
        else:
             main_instance_running = True
        
        # 실제 반복 실행
        if main_instance_running:
            main()