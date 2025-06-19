import os
import re
import sys
import time
import json # json 모듈 추가
import requests
from googleapiclient.discovery import build

# --- 설정 부분 ---
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# 감시할 채널들을 쉼표로 구분하여 입력 (@핸들 또는 채널ID)
TARGET_CHANNELS = os.environ.get("TARGET_CHANNELS")

# 체크 간격 (초 단위)
CHECK_INTERVAL_SECONDS = 300 # 5분

# 상태 저장 파일 이름
STATE_FILE = "channel_states.json"

# --- 상태 관리 함수 ---
def load_channel_states():
    """JSON 파일에서 채널별 마지막 영상 ID 상태를 불러옴"""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"경고: {STATE_FILE}이 비어있거나 손상되었습니다. 새로운 상태 파일을 생성합니다.")
        return {}

def save_channel_states(states):
    """채널 상태를 JSON 파일에 저장"""
    with open(STATE_FILE, "w") as f:
        json.dump(states, f, indent=4)

# --- 식별자 변환 및 정보 조회 함수 ---
def get_channel_id_from_handle(handle, youtube_service):
    """@핸들을 사용하여 채널 ID를 조회"""
    if handle.startswith('@'):
        handle = handle[1:]
    try:
        # 이 부분이 올바르게 수정되었습니다. .list()가 추가되었습니다.
        search_response = youtube_service.search().list(
            q=handle,
            type='channel',
            part='id',
            maxResults=1
        ).execute()
        
        if not search_response.get('items'):
            return None
        return search_response['items'][0]['id']['channelId']
    except Exception as e:
        print(f"'{handle}' 핸들 조회 중 오류 발생: {e}")
        return None

def resolve_identifier_to_id(identifier, youtube_service):
    """@핸들 또는 채널ID를 받아 최종 채널ID를 반환"""
    identifier = identifier.strip()
    if not identifier:
        return None
    
    if identifier.startswith('@'):
        print(f"'{identifier}' 핸들을 채널 ID로 변환합니다...")
        channel_id = get_channel_id_from_handle(identifier, youtube_service)
        if channel_id:
            print(f" -> 변환 성공: {channel_id}")
        else:
            print(f" -> 변환 실패: 채널을 찾을 수 없습니다.")
        return channel_id
    elif identifier.startswith('UC'):
        print(f"'{identifier}'는 채널 ID입니다. 그대로 사용합니다.")
        return identifier
    else:
        print(f"경고: '{identifier}'는 알 수 없는 형식의 식별자입니다. 건너뜁니다.")
        return None

def get_latest_video(channel_id, youtube_service):
    """특정 채널 ID의 최신 영상을 가져옵니다."""
    try:
        channel_response = youtube_service.channels().list(id=channel_id, part='contentDetails').execute()
        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        playlist_response = youtube_service.playlistItems().list(playlistId=uploads_playlist_id, part='snippet', maxResults=1).execute()
        if not playlist_response.get('items'):
            return None
        return playlist_response['items'][0]['snippet']
    except Exception as e:
        print(f"'{channel_id}' 채널의 영상 조회 중 오류 발생: {e}")
        return None

# --- 디스코드 및 기타 유틸리티 함수 ---
# unshorten_url, process_description, send_to_discord 함수는 이전과 동일
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

# --- 메인 함수 ---
def main():
    print("다중 채널 유튜브-디스코드 알림 봇을 시작합니다.")
    
    if not all([YOUTUBE_API_KEY, DISCORD_WEBHOOK_URL, TARGET_CHANNELS]):
       print("오류: 환경 변수(YOUTUBE_API_KEY, DISCORD_WEBHOOK_URL, TARGET_CHANNELS)가 올바르게 설정되지 않았습니다.")
       sys.exit(1)

    youtube_service = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    
    initial_targets = [identifier.strip() for identifier in TARGET_CHANNELS.split(',')]
    resolved_channel_ids = []
    print("설정된 채널들의 ID를 확인합니다...")
    for identifier in initial_targets:
        if not identifier: continue
        channel_id = resolve_identifier_to_id(identifier, youtube_service)
        if channel_id: resolved_channel_ids.append(channel_id)
    
    if not resolved_channel_ids: print("감시할 유효한 채널이 없습니다. 봇을 종료합니다."); sys.exit(1)
        
    print("-" * 30); print(f"감시를 시작할 채널 목록 ({len(resolved_channel_ids)}개):"); [print(f"- {cid}") for cid in resolved_channel_ids]; print("-" * 30)

    while True:
        states = load_channel_states(); states_updated = False
        for channel_id in resolved_channel_ids:
            print(f"\n--- '{channel_id}' 채널 확인 중 ---")
            latest_video = get_latest_video(channel_id, youtube_service)
            if not latest_video: print("최신 영상을 가져올 수 없거나 채널에 영상이 없습니다."); continue
            current_video_id = latest_video['resourceId']['videoId']; last_known_video_id = states.get(channel_id)
            if last_known_video_id is None:
                print(f"'{channel_id}' 채널을 처음 확인합니다. 기준 영상 ID를 저장합니다: {current_video_id}")
                states[channel_id] = current_video_id; states_updated = True; continue
            if current_video_id != last_known_video_id:
                print(f"!!! 새로운 영상 발견 !!! (이전: {last_known_video_id}, 현재: {current_video_id})")
                send_to_discord(latest_video); states[channel_id] = current_video_id; states_updated = True
            else: print("새로운 영상이 없습니다.")
        
        if states_updated: print("\n상태 파일 업데이트 중..."); save_channel_states(states); print("상태 파일 업데이트 완료.")
        print(f"\n모든 채널 확인 완료. {CHECK_INTERVAL_SECONDS}초 후에 다시 확인합니다.")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()