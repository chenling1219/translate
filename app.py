from flask import Flask, request
import json, random, requests, os, time, re
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
from bs4 import BeautifulSoup
import urllib.parse
from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI
import pdfplumber
import tempfile

random_list = []
last_msg = ""
memlist = ""
user_pdf_data = {}  

app = Flask(__name__)

# -------- LINE BOT 憑證 --------
access_token = os.getenv("access_token")
channel_secret = os.getenv("channel_secret")
line_bot_api = LineBotApi(access_token)
line_handler = WebhookHandler(channel_secret)

# 翻譯
API_KEY = os.getenv("API_KEY")
ENDPOINT = os.getenv("ENDPOINT")
REGION = os.getenv("REGION")

# money
def setup_sheets_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials_dict = {
        "type": "service_account",
        "project_id": os.getenv("project_id_money"),
        "private_key_id": os.getenv("private_key_id_money"),
        "private_key": os.getenv("private_key_money").replace('\\n', '\n'),
        "client_email": os.getenv("client_email_money"),
        "client_id": os.getenv("client_id_money"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.getenv("client_x509_cert_url_money"),
        "universe_domain": "googleapis.com"
    }
    creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
    client = gspread.authorize(creds)
    return client
sheets_client = setup_sheets_client()
user_data = {}

# -------- ChatPDF Functions --------
def extract_pdf_text(file_path):
    """Extract text from a PDF file using pdfplumber."""
    try:
        with pdfplumber.open(file_path) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text
    except Exception as e:
        print(f"PDF extraction failed: {e}")
        return None

# 初始化 OpenAI 客戶端
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 在檔案頂部加入快取相關匯入
from cachetools import TTLCache
from aiolimiter import AsyncLimiter

# 初始化快取（TTL 快取，儲存 5 分鐘）
query_cache = TTLCache(maxsize=1000, ttl=300)

# 初始化速率限制（每分鐘最多 20 次請求）
rate_limiter = AsyncLimiter(20, 60)  # 每 60 秒最多 20 次請求

async def process_pdf_query(pdf_text, query):
    """使用 OpenAI 處理用戶對 PDF 內容的查詢，並加入快取和速率限制"""
    if not pdf_text:
        return "沒有可用的 PDF 內容。"
    
    cache_key = f"{pdf_text[:50]}{query}"
    if cache_key in query_cache:
        return query_cache[cache_key]
    
    try:
        # 應用速率限制
        async with rate_limiter:
            prompt = f"以下是 PDF 內容：\n\n{pdf_text}\n\n用戶問題：{query}\n\n請根據 PDF 內容回答問題，並以繁體中文回覆。"
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "你是一個能閱讀 PDF 內容並回答問題的助手。"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.7
            )
            answer = response.choices[0].message.content.strip()
            query_cache[cache_key] = answer
            return answer if answer else "無法根據 PDF 內容回答你的問題。"
    except Exception as e:
        print(f"OpenAI 處理失敗: {e}")
        error_msg = str(e)
        if "insufficient_quota" in error_msg:
            return "很抱歉，目前 OpenAI API 配額已用完，請稍後再試或聯繫管理員升級計畫。"
        return f"處理查詢時發生錯誤：{error_msg}"


# -------- 抽籤功能 --------
def foodpush():
    food = TextSendMessage(
        text='食物!',
        quick_reply=QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label='拉麵', text="拉麵")),
                QuickReplyButton(action=MessageAction(label='咖哩飯', text="咖哩飯")),
                QuickReplyButton(action=MessageAction(label='滷肉飯', text="滷肉飯")),
                QuickReplyButton(action=MessageAction(label='義大利麵', text="義大利麵")),
                QuickReplyButton(action=MessageAction(label='披薩', text="披薩")),
                QuickReplyButton(action=MessageAction(label='鍋燒意麵', text="鍋燒意麵")),
                QuickReplyButton(action=MessageAction(label='燒烤', text="燒烤")),
                QuickReplyButton(action=MessageAction(label='牛肉麵', text="牛肉麵")),
                QuickReplyButton(action=MessageAction(label='鱔魚意麵', text="鱔魚意麵")),
                QuickReplyButton(action=MessageAction(label='牛排', text="牛排")),
            ]
        )
    )
    return food

def drinkpush():
    drink = TextSendMessage(
        text='飲料店!',
        quick_reply=QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label='五十嵐', text="五十嵐")),
                QuickReplyButton(action=MessageAction(label='珍煮丹', text="珍煮丹")),
                QuickReplyButton(action=MessageAction(label='春水堂', text="春水堂")),
                QuickReplyButton(action=MessageAction(label='鶴茶樓', text="鶴茶樓")),
                QuickReplyButton(action=MessageAction(label='麻古茶坊', text="麻古茶坊")),
                QuickReplyButton(action=MessageAction(label='五桐號', text="五桐號")),
                QuickReplyButton(action=MessageAction(label='迷客夏', text="迷客夏")),
                QuickReplyButton(action=MessageAction(label='CoCo', text="CoCo")),
            ]
        )
    )
    return drink

def listpush():
    plist = TextSendMessage(
        text='推薦清單',
        quick_reply=QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label='吃的', text="吃什麼")),
                QuickReplyButton(action=MessageAction(label='喝的', text="喝什麼")),
            ]
        )
    )
    return plist

def randomone(tk, msg, last_msg_01, memlist):
    if msg == '開始抽籤吧':
        res = random.choice(random_list)
        line_bot_api.reply_message(tk, TextSendMessage(text='抽選結果為' + res))
        memlist = ""
        last_msg_01 = ""
    elif msg == '清空清單':
        random_list.clear()
        line_bot_api.reply_message(tk, TextSendMessage(text='已清空抽選清單'))
    elif msg == '給我一些想法':
        line_bot_api.reply_message(tk, listpush())
    elif msg == '吃什麼':
        line_bot_api.reply_message(tk, foodpush())
        memlist = "foodlist"
    elif msg == '喝什麼':
        line_bot_api.reply_message(tk, drinkpush())
        memlist = "drinklist"
    else:
        if memlist == "foodlist":
            random_list.append(msg)
            back_01 = [
                TextSendMessage(text = msg+' 已加入抽選清單 (  OuO)b'),
                foodpush()
                ]
            line_bot_api.reply_message(tk, back_01)
        elif memlist == "drinklist":
            random_list.append(msg)
            back_02 = [
                TextSendMessage(text = msg+' 已加入抽選清單 (  oTo)b'),
                drinkpush()
                ]
            line_bot_api.reply_message(tk, back_02)
        else:
            random_list.append(msg)
    return last_msg_01, memlist

# -------- 天氣查詢功能 --------
def weather(address):
    def nowWeather(address):
        result = {}
        code = 'CWA-9ECE9E2D-1DF4-45DB-8999-FAC76234B2A3'

        # 即時天氣
        try:
            urls = [
                f'https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={code}',
                f'https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001?Authorization={code}'
            ]
            for url in urls:
                req = requests.get(url) 
                data = req.json()
                station = data['records']['Station']
                for i in station:
                    city = i['GeoInfo']['CountyName']
                    area = i['GeoInfo']['TownName']
                    key = f'{city}{area}'
                    if key not in result:
                        weather = i['WeatherElement']['Weather']
                        temp = i['WeatherElement']['AirTemperature']
                        humid = i['WeatherElement']['RelativeHumidity']
                        #if({weather}==-99):
                        #    result[key] = f'目前溫度 {temp}°C，相對濕度 {humid}%'
                        if ((weather == -99) or (temp == -99) or (temp == -99)):
                            result[key] = f'目前資料有誤請稍後再試'
                        else:
                            result[key] = f'目前天氣：{weather}，溫度 {temp}°C，相對濕度 {humid}%'
        except Exception as e:
            print("即時天氣抓取失敗:", e)


        # 回傳結果
        output = '找不到氣象資訊'
        for key, value in result.items():
            if key in address:
                output = f'{value}'
                #output = f'{value}'
                break

        return output
    
    def futureWeather(address):
        result = {}
        code = 'CWA-9ECE9E2D-1DF4-45DB-8999-FAC76234B2A3'

        # 未來12小時天氣
        try:
            url = f'https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001?Authorization={code}'
            req = requests.get(url)
            data = req.json()
            
            locations = data['records']['location']
            for loc in locations:
                city = loc['locationName']
                weather_elements = loc['weatherElement']
                
                # weather_elements 每個是不同類型：Wx(天氣狀況)、PoP(降雨機率)、MinT(最低溫)、MaxT(最高溫)、CI(舒適度)
                weather_info = {}
                for element in weather_elements:
                    element_name = element['elementName']
                    weather_info[element_name] = element['time'][0]['parameter']['parameterName']  # 取未來第一個時段
                
                key = f'{city}'
                if key not in result:
                    # 判斷資料是否完整
                    if ('Wx' not in weather_info) or ('PoP' not in weather_info) or ('MinT' not in weather_info) or ('MaxT' not in weather_info):
                        result[key] = f'目前資料有誤請稍後再試'
                    else:
                        result[key] = f"未來12小時天氣：{weather_info['Wx']}，降雨機率 {weather_info['PoP']}%，溫度 {weather_info['MinT']}°C ~ {weather_info['MaxT']}°C"
        except Exception as e:
            print("未來12小時天氣抓取失敗:", e)



        # 回傳結果
        output = '找不到氣象資訊'
        for key, value in result.items():
            if key in address:
                output = f'{value}'
                break

        return output
    
    def air(address):
        result = {}

        # 空氣品質
        try:
            aqi_url = 'https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key=eba9f0a9-069d-4d66-bfe6-733dcefa4302&limit=1000&format=JSON'
            req = requests.get(aqi_url)
            data = req.json()
            records = data['records']
            aqi_status = ["良好", "普通", "對敏感族群不健康", "對所有族群不健康", "非常不健康", "危害"]
            
            # 建立縣市的第一筆資料
            county_first_record = {}

            for item in records:
                county = item['county']
                if county not in county_first_record:
                    aqi = int(item['aqi'])
                    status = aqi_status[min(aqi // 50, 5)]
                    county_first_record[county] = f'空氣品質{status}，AQI：{aqi}。'

        except Exception as e:
            print("空氣品質抓取失敗:", e)

        # 回傳結果
        output = '找不到氣象資訊'
        for county, info in county_first_record.items():
            if county in address:
                output = info
                break

        return output
    result = f"{nowWeather(address)}\n\n{futureWeather(address)}\n\n{air(address)}\n\n🔗 [詳細內容請見中央氣象署官網](https://www.cwa.gov.tw/)'"
    return result

# -------- 翻譯功能 --------
def azure_translate(user_input, to_language):
    if to_language == None:
        return "Please select a language"
    else:
        apikey = os.getenv("API_KEY")
        endpoint = os.getenv("ENDPOINT")
        region = os.getenv("REGION")
        credential = AzureKeyCredential(apikey)
        text_translator = TextTranslationClient(credential=credential, endpoint=endpoint, region=region)
        
        try:
            response = text_translator.translate(body=[user_input], to_language=[to_language])
            print(response)
            translation = response[0] if response else None
            if translation:
                detected_language = translation.detected_language
                result = ''
                if detected_language:
                    print(f"偵測到輸入的語言: {detected_language.language} 信心分數: {detected_language.score}")
                for translated_text in translation.translations:
                    result += f"翻譯成: '{translated_text.to}'\n結果: '{translated_text.text}'"
                return result

        except HttpResponseError as exception:
            if exception.error is not None:
                print(f"Error Code: {exception.error.code}")
                print(f"Message: {exception.error.message}")
                
def chooseLen(tk, msg):
    back_03 = [
        TextSendMessage(text = '請選擇要翻譯的語言:',
        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=PostbackAction(label="英文",data=f"lang=en&text={msg}",display_text="英文")),
                QuickReplyButton(action=PostbackAction(label="日文",data=f"lang=ja&text={msg}",display_text="日文")),
                QuickReplyButton(action=PostbackAction(label="韓文",data=f"lang=ko&text={msg}",display_text="韓文")),
                QuickReplyButton(action=PostbackAction(label="繁體中文",data=f"lang=zh-Hant&text={msg}",display_text="繁體中文")),
                QuickReplyButton(action=PostbackAction(label="簡體中文",data=f"lang=zh-Hans&text={msg}",display_text="簡體中文")),
                QuickReplyButton(action=PostbackAction(label="文言文",data=f"lang=lzh&text={msg}",display_text="文言文")),
                QuickReplyButton(action=PostbackAction(label="法文",data=f"lang=fr&text={msg}",display_text="法文")),
                QuickReplyButton(action=PostbackAction(label="西班牙文",data=f"lang=es&text={msg}",display_text="西班牙文")),
                QuickReplyButton(action=PostbackAction(label="阿拉伯文",data=f"lang=ar&text={msg}",display_text="阿拉伯文")),
                QuickReplyButton(action=PostbackAction(label="德文",data=f"lang=de&text={msg}",display_text="德文"))
            ]
        ))
    ]
    line_bot_api.reply_message(tk, back_03)
                
# -------- 記帳功能 --------
# 開啟指定的 Google 試算表
sheet = sheets_client.open("python money").sheet1

def choose():
    choose = TextSendMessage(
        text='請選擇分類',
        quick_reply=QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label='餐飲', text="餐飲")),
                QuickReplyButton(action=MessageAction(label='交通', text="交通")),
                QuickReplyButton(action=MessageAction(label='購物', text="購物")),
                QuickReplyButton(action=MessageAction(label='醫療', text="醫療")),
                QuickReplyButton(action=MessageAction(label='娛樂', text="娛樂")),
                QuickReplyButton(action=MessageAction(label='其他', text="其他")),
            ]
        )
    )
  
    return choose

def money(tk, msg, user_id):
    if msg == '我要記帳':
        line_bot_api.reply_message(tk, choose())
        user_data[user_id] = {"category": None, "amount": None}
    elif msg in ["餐飲", "交通", "購物","醫療","娛樂", "其他"]:
        if user_id in user_data:  # 確認用戶有先執行「我要記帳」
            user_data[user_id]["category"] = msg
            line_bot_api.reply_message(tk, TextSendMessage(text=f'你選擇了 {msg} 類別，請輸入金額。'))
        else:
            line_bot_api.reply_message(tk, TextSendMessage(text='請先輸入『我要記帳』開始記帳流程。'))
    elif msg.isdigit():  
        if user_id in user_data and user_data[user_id]["category"]:
            user_data[user_id]["amount"] = int(msg)
            category = user_data[user_id]["category"]
            amount = user_data[user_id]["amount"]
            # 將資料寫入 Google Sheets
            tz_utc_8 = timezone(timedelta(hours=8))
            now = datetime.now(tz_utc_8).strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([now, user_id, category, amount])

            line_bot_api.reply_message(tk, TextSendMessage(text=f'已記錄 {category}: {amount} 元！'))
            del user_data[user_id]
        else:
            line_bot_api.reply_message(tk, TextSendMessage(text='請先選擇分類'))
    elif msg == "查詢":
        # 從 Google Sheet 讀取所有資料
        records = sheet.get_all_values()
        header = records[0]
        data = records[1:]

        
        user_records = [row for row in data if row[1] == user_id]
        last_five = user_records[-5:]

        if not last_five:
            line_bot_api.reply_message(tk, TextSendMessage(text='目前沒有記帳紀錄。'))
        else:
            reply_lines = ['你最近的記帳紀錄：']
            for row in last_five:
                reply_lines.append(f"{row[0]} - {row[2]}: {row[3]} 元")
            line_bot_api.reply_message(tk, TextSendMessage(text='\n'.join(reply_lines)))
    elif msg == '查詢類別':
        line_bot_api.reply_message(tk, choose(2,''))
        
    elif msg.startswith("查 "):
        
        category_to_check = msg.replace("查 ", "").strip()

        if category_to_check not in ["餐飲", "交通", "購物","醫療","娛樂", "其他"]:
            line_bot_api.reply_message(tk, TextSendMessage(text='請輸入正確的分類（餐飲、交通、娛樂、其他）'))
        else:
            
            records = sheet.get_all_values()[1:]
            
            user_records = [row for row in records if row[1] == user_id and row[2] == category_to_check]
            
            last_five = user_records[-5:]

            if not last_five:
                line_bot_api.reply_message(tk, TextSendMessage(text=f'你在『{category_to_check}』分類中沒有紀錄。'))
            else:
                reply_lines = [f"你在『{category_to_check}』分類的最近紀錄："]
                for row in last_five:
                    reply_lines.append(f"{row[0]}: {row[3]} 元")
                line_bot_api.reply_message(tk, TextSendMessage(text='\n'.join(reply_lines)))
    elif msg.startswith("查詢日期 "):
        
        date_to_check = msg.replace("查詢日期 ", "").strip()

        try:
            
            datetime.strptime(date_to_check, "%Y-%m-%d")
        except ValueError:
            line_bot_api.reply_message(tk, TextSendMessage(text='請使用正確的日期格式（例如：2025-04-01）'))
            return

        
        records = sheet.get_all_values()[1:]  
        user_records = [row for row in records if row[1] == user_id and row[0].startswith(date_to_check)]

        if not user_records:
            line_bot_api.reply_message(tk, TextSendMessage(text=f'你在 {date_to_check} 沒有記帳紀錄。'))
        else:
            total_amount = sum(int(row[3]) for row in user_records if row[3].isdigit())
            reply_lines = [f"{date_to_check} 的記帳紀錄："]
            for row in user_records:
                reply_lines.append(f"{row[2]}：{row[3]} 元")
            reply_lines.append(f"\n💰 總支出：{total_amount} 元")
            line_bot_api.reply_message(tk, TextSendMessage(text='\n'.join(reply_lines)))
    elif msg.startswith("查詢月 "):

         month_to_check = msg.replace("查詢月 ", "").strip()

         try:

            start_date = datetime.strptime(month_to_check, "%Y-%m")
         except ValueError:
            line_bot_api.reply_message(tk, TextSendMessage(text='請使用正確的日期格式（例如：2025-04）'))
            return


         start_of_month = start_date.replace(day=1)  # 該月的起始日期（1日）

         end_of_month = (start_of_month.replace(month=start_of_month.month % 12 + 1) - timedelta(days=1))

         start_of_month_str = start_of_month.strftime("%Y-%m-%d")
         end_of_month_str = end_of_month.strftime("%Y-%m-%d")


         records = sheet.get_all_values()[1:]  # 不要標題列
         user_records = [row for row in records if row[1] == user_id and start_of_month_str <= row[0][:10] <= end_of_month_str]

         if not user_records:
             line_bot_api.reply_message(tk, TextSendMessage(text=f'你在 {start_of_month_str} 到 {end_of_month_str} 期間沒有記帳紀錄。'))
         else:
             total_amount = sum(int(row[3]) for row in user_records if row[3].isdigit())
             reply_lines = [f"你在 {start_of_month_str} 到 {end_of_month_str} 期間的記錄："]
             for row in user_records:
                 reply_lines.append(f"{row[0]} - {row[2]}: {row[3]} 元")
             reply_lines.append(f"\n💰 總支出：{total_amount} 元")
             line_bot_api.reply_message(tk, TextSendMessage(text='\n'.join(reply_lines)))
    elif msg.startswith("查詢月類別 "):
         parts = msg.replace("查詢月類別 ", "").strip().split()


         if len(parts) == 1:
             month_str = parts[0]
             try:
                 datetime.strptime(month_str, "%Y-%m")
             except ValueError:
                 line_bot_api.reply_message(tk, TextSendMessage(text='請使用正確的日期格式（例如：2025-04）'))
                 return
             line_bot_api.reply_message(tk, choose(3,month_str))
             return


         elif len(parts) == 2:
             month_str, category = parts
             try:
                start_date = datetime.strptime(month_str, "%Y-%m")
             except ValueError:
                line_bot_api.reply_message(tk, TextSendMessage(text='請使用正確的日期格式（例如：2025-04）'))
                return


             start_of_month = start_date.replace(day=1)
             if start_of_month.month == 12:
                 next_month = start_of_month.replace(year=start_of_month.year + 1, month=1, day=1)
             else:
                 next_month = start_of_month.replace(month=start_of_month.month + 1, day=1)
             end_of_month = next_month - timedelta(days=1)

             start_str = start_of_month.strftime("%Y-%m-%d")
             end_str = end_of_month.strftime("%Y-%m-%d")


             records = sheet.get_all_values()[1:]  
             filtered = [
                 row for row in records
                 if row[1] == user_id and row[2] == category and start_str <= row[0][:10] <= end_str
                 ]

             if not filtered:
                 line_bot_api.reply_message(tk, TextSendMessage(text=f'{month_str} 在『{category}』分類中沒有記帳紀錄。'))
             else:
                 total_amount = sum(int(row[3]) for row in filtered if row[3].isdigit())
                 reply_lines = [f"{month_str} 在『{category}』分類的記錄："]
                 for row in filtered:
                     reply_lines.append(f"{row[0]}: {row[3]} 元")
                 reply_lines.append(f"\n💰 總支出：{total_amount} 元")
                 line_bot_api.reply_message(tk, TextSendMessage(text='\n'.join(reply_lines)))
    # 其他無效輸入
    
    else:
        line_bot_api.reply_message(tk, TextSendMessage(text='請輸入關鍵字來進行記帳操作\n- 我要記帳\n- 查詢\n- 查 {類別}\n- 查詢日期 YYYY-MM-DD\n- 查詢月 YYYY-MM\n- 查詢月類別 YYYY-MM {類別}'))

# -------- 查詢附近美食 --------
main_menu = {
    "附近美食": ["1公里內4★以上", "3公里內4.2★以上", "5公里內4.5★以上"],
    "附近景點": ["1公里內3.5★以上", "3公里內4★以上", "5公里內4.2★以上", "10公里內4.5★以上"],
    "各地美食": "",
    "各地景點": ""
}

# 台灣縣市
counties_list = [["台北市", "新北市", "基隆市"],  ["桃園市", "新竹縣", "新竹市"],   
                 ["宜蘭縣", "苗栗縣", "台中市"],  ["彰化縣", "雲林縣", "南投縣"], 
                 ["嘉義縣", "嘉義市", "台南市"],  ["高雄市", "屏東縣", "澎湖縣"],   
                 ["花蓮縣", "台東縣", "金門縣"]]    

# 餐廳類別
meals_list = [["中式料理","日式料理","居酒屋"], ["義式料理","港式料理","美式料理"], 
              ["韓式","泰式","小吃"],           ["精緻高級","約會餐廳","餐酒館"],
              ["早餐","早午餐","宵夜"],         ["火鍋","燒肉","牛排"],
              ["拉麵","咖哩","素食"],           ["甜點","冰品飲料","飲料店"]]

county_sno = {"台北市":"0001090", "新北市":"0001091", "基隆市":"0001105", "桃園市":"0001107", "新竹縣":"0001108", "新竹市":"0001109", "宜蘭縣":"0001106",
              "苗栗縣":"0001110", "台中市":"0001112", "彰化縣":"0001113", "雲林縣":"0001115", "南投縣":"0001114", "嘉義縣":"0001116", "嘉義市":"0001117",
              "台南市":"0001119", "高雄市":"0001121", "屏東縣":"0001122", "澎湖縣":"0001125", "花蓮縣":"0001124", "台東縣":"0001123", "金門縣":"0001126"}
              
attraction_cate = {"無障礙旅遊":"26", "旅遊景點":"27", "溫泉景點":"28", "藝文展館":"29", "夜市老街":"30", "古蹟寺廟":"31", "遊樂區":"32", "樂齡旅遊":"34"}

def foodie(tk, user_id, result):
    if result[0] in main_menu:
        if result[0] == '附近美食' or result[0] == '附近景點':
            if len(result) == 1:  # 尚未選範圍
                location_ok = 'N'
                if os.path.exists('/tmp/'+user_id+'.txt'):
                    with open('/tmp/'+user_id+'.txt', 'r') as f:
                        line = f.readline()
                        data = line.strip().split(',')
                        old_timestamp = int(data[2])
                    current_timestamp = int(time.time())
                    if current_timestamp - old_timestamp < 600:
                        location_ok = 'Y'
                
                if location_ok == 'Y':                
                    ranges = main_menu[result[0]]
                    buttons_template = ButtonsTemplate(
                        title="選擇範圍", 
                        text="請選擇" + result[0] + "多大的範圍", 
                        actions=[MessageAction(label=range, text=result[0] + " " + range) for range in ranges]
                    )
                    template_message = TemplateSendMessage(alt_text="選擇範圍", template=buttons_template)
                    line_bot_api.reply_message(tk, template_message)
                else:
                    line_bot_api.reply_message(tk, TextSendMessage(text='需要分享你的位置資訊才能進行查詢'))
            else:
                with open('/tmp/'+user_id+'.txt', 'r') as f:
                    line = f.readline()
                    data = line.strip().split(',')
                    latitude = data[0]
                    longitude = data[1]
                if result[0] == '附近美食':
                    type = 'restaurant'    # 餐廳
                else:
                    type = 'tourist_attraction'   # 旅遊景點
                pat = r'(\d+)公里內([\d|.]+)★以上'
                match = re.search(pat,result[1])
                radius = int(match.group(1)) * 1000
                stars = match.group(2)
                API_KEY_foodie = os.getenv('API_KEY_foodie')
                # Google Places API URL
                url = f'https://maps.googleapis.com/maps/api/place/nearbysearch/json'
                pagetoken = None
                target = ''
                cc=0
                while True:
                    # 設定請求的參數
                    params = {
                        'location': f'{latitude},{longitude}',  # 經緯度
                        'radius': radius,  # 半徑，單位是米
                        'type': type,  # 類型設置為餐廳
                    #    'keyword': '美食',  # 關鍵字，這裡你可以設置為美食
                        'language': 'zh-TW',
                        'key': API_KEY_foodie,  # 你的 API 金鑰
                        'rankby': 'prominence',  # prominence:按受歡迎程度排序/distance：按距離排序  
                    #    'opennow': 'true',  # 查詢當前開放的餐廳
                    }
                    if pagetoken:
                        params['pagetoken'] = pagetoken
                    # 發送請求到 Google Places API
                    response = requests.get(url, params=params)
                    # 解析回應的 JSON 數據
                    data = response.json() 
                    cc = cc + 1                    
                    # 取出附近標的物的名稱、地址、評價
                    if data['status'] == 'OK':
                        for place in data['results']:    # name, vicinity, geometry.location(lat,lng), rating, user_ratings_total, price_level, formatted_address
                            name = place['name']
                            address = place.get('vicinity', '無地址')
                            rating = place.get('rating', 0)
                            if rating > float(stars):
                                target += f"【{name}】{rating}★\n{address}\n"
                        #if cc == 1:
                        #    line_bot_api.reply_message(tk,TextSendMessage(text=target))
                        #    return                       
                        pagetoken = data.get('next_page_token')   # 一次20筆，是否有下一頁
                        if not pagetoken:
                            break     # 沒有下一頁，跳出迴圈                  
                        time.sleep(2)   # 有下一頁，等待幾秒鐘（如：2秒鐘以上，避免超過 API 請求限制）
                if target == '':                            
                    target = "無法找到" + result[0]                
                line_bot_api.reply_message(tk,TextSendMessage(text=target)) 
        elif len(result) == 1:  # (result[0] == '各地美食' or result[0] == '各地景點') 且 尚未選縣市
            # 用 loop 建立 CarouselColumn 的 list
            columns = []
            regions = main_menu[result[0]]
            for counties in counties_list:
                column = CarouselColumn(
                    #thumbnail_image_url='https://example.com/sample.jpg',  # 可選
                    #title = "選擇縣市",  # 可選
                    text = "請選擇行政區域",
                    actions=[MessageAction(label=county, text=result[0] + " " + county) for county in counties]
                )
                columns.append(column)
            carousel_template = CarouselTemplate(columns=columns)
            template_message = TemplateSendMessage(alt_text="選擇縣市", template=carousel_template)
            line_bot_api.reply_message(tk, template_message)
        elif result[0] == '各地美食':           
            if len(result) == 2:  # 選完縣市
                # 用 loop 建立 CarouselColumn 的 list
                columns = []
                for meals in meals_list:
                    column = CarouselColumn(
                        #thumbnail_image_url='https://example.com/sample.jpg',  # 可選
                        #title = "選擇餐種",  # 可選
                        text = "請選擇" + result[1] + "餐廳類別",
                        actions=[MessageAction(label=meal, text=result[0] + " " + result[1] + " " + meal) for meal in meals]
                    )
                    columns.append(column)
                carousel_template = CarouselTemplate(columns=columns)
                template_message = TemplateSendMessage(alt_text="選擇餐廳類別", template=carousel_template)
                line_bot_api.reply_message(tk, template_message)              
            elif len(result) == 3:  # 選完餐廳類別
                url = 'https://ifoodie.tw/explore/' + result[1] +'/list/' + result[2]
                encoded_url = urllib.parse.quote(url, safe=":/") + '?sortby=popular'
                response = requests.get(encoded_url)
                soup = BeautifulSoup(response.text,'html.parser') 
                infos = soup.select('.restaurant-info')
                target = ''
                columns = []
                count = 0
                for info in infos:
                    sp = BeautifulSoup(str(info),'html.parser')
                    rid = sp.select_one('.restaurant-info').get('class')[0]
                    index = sp.select_one('.index').text
                    name = sp.select_one('.title-text').text
                    rating = sp.select_one('.text').text
                    not_open_now = sp.select_one('.info')
                    if not_open_now:
                        not_open_now = not_open_now.text + "\n"
                    else:
                        not_open_now = ""
                    avg_price = sp.select_one('.avg-price')
                    if avg_price:
                        avg_price = avg_price.text[2:]   # · 均消 $350，去掉前兩個字元
                    else:
                        avg_price = ""
                    address = sp.select_one('.address-row').text
                    href = "https://ifoodie.tw/" + sp.select_one('.title-text').get('href')
                    cover='cover'
                    img = sp.select_one(f'.{rid}.{cover}')
                    img = img.get('data-src') or img.get('src')
                    target += f"{index} {name} {rating}★\n{address}\n"
                    title = name + "\n" + address
                    text = rating + "★ " + avg_price + "\n" + not_open_now
                    if len(title)>40:
                        title = title[:37] + "..."  # 省略太長的部分
                    if len(text)>60:
                        text = text[:57] + "..."  # 省略太長的部分
                    column = CarouselColumn(
                        thumbnail_image_url = img,  # 可選
                        title = title,  # 可選, 40字元
                        text = text,  # 有title：60字元，無title：120字元
                        actions=[URIAction(label="WEBSITE", uri=href)]
                    )
                    columns.append(column)
                    count = count + 1
                    if count == 10:
                        break
                if target == '': 
                    target = "無法找到"               
                    line_bot_api.reply_message(tk,TextSendMessage(text=target)) 
                else:
                    carousel_template = CarouselTemplate(columns=columns)
                    template_message = TemplateSendMessage(alt_text="顯示餐廳", template=carousel_template)
                    line_bot_api.reply_message(tk, template_message)              
        elif result[0] == '各地景點':           
            if len(result) == 2:  # 選完縣市
                sno = county_sno[result[1]]
                url = 'https://www.taiwan.net.tw/m1.aspx?sno=' + sno
                response = requests.get(url)
                soup = BeautifulSoup(response.text,'html.parser') 

                columns = []
                actions = []                
                radios = soup.select('.category-radio')
                for radio in radios:
                    text = radio.text.strip()
                    if text != 'All':
                        actions.append(MessageAction(label=text, text=result[0] + " " + result[1] + " " + text))                        
                        if len(actions) == 3:
                            column = CarouselColumn(
                                text = "請選擇" + result[1] + "景點類別",
                                actions = actions
                            )
                            columns.append(column)
                            actions = []           
                n = 3 - (len(actions) % 3)
                if n < 3:
                    for i in range(0, n):
                        #actions.append(URIAction(uri='#'))
                        actions.append(MessageAction(label=" ", text=result[0] + " " + result[1]))   # label不能空值                      
                    column = CarouselColumn(
                        text = "請選擇" + result[1] + "景點類別",
                        actions = actions
                    )
                    columns.append(column)
                carousel_template = CarouselTemplate(columns=columns)
                template_message = TemplateSendMessage(alt_text="選擇景點類別", template=carousel_template)
                line_bot_api.reply_message(tk, template_message)              
            elif len(result) == 3:  # 選完景點類別
                sno = county_sno[result[1]]
                url = 'https://www.taiwan.net.tw/m1.aspx?sno=' + sno
                response = requests.get(url)
                soup = BeautifulSoup(response.text,'html.parser') 
                
                cid = attraction_cate[result[2]]
                attrs = soup.select('.col-12_sm-6_md-3')
                target = ''
                columns = []
                count = 0
                for attr in attrs:
                    sp = BeautifulSoup(str(attr),'html.parser')
                    data_type = sp.select_one('.col-12_sm-6_md-3').get('data-type')
                    if cid in data_type:
                        href = sp.select_one('.card-link').get('href')
                        href = href.replace("amp;", "")
                        href = 'https://www.taiwan.net.tw/' + href
                        view = sp.select_one('.view-badge').text.strip()
                        title = sp.select_one('.card-title').text.strip()
                        target += f"{title} {view}\n"
                        img = sp.select_one('img').get('data-src')
                        tags = sp.select('.hashtag a')
                        tag_list = ""
                        for tag in tags:
                            if tag_list != "":
                                tag_list += ", "
                            tag_list += tag.text  
                        #mmm = title + " " + view + '\n' + tag_list + '\n' + href
                        #line_bot_api.reply_message(tk,TextSendMessage(text=mmm)) 
                        #return
                        column = CarouselColumn(
                            thumbnail_image_url = img,  # 可選
                            title = title,  # 可選, 40字元
                            text = '點閱次數：' + view + '\n' + '標籤：' + tag_list,  # 有title：60字元，無title：120字元
                            actions=[URIAction(label="WEBSITE", uri=href)]
                        )
                        columns.append(column)
                        count = count + 1
                        if count == 10:
                            break
                if target == '': 
                    target = "無法找到"               
                    line_bot_api.reply_message(tk,TextSendMessage(text=target)) 
                else:
                    carousel_template = CarouselTemplate(columns=columns)
                    template_message = TemplateSendMessage(alt_text="顯示景點", template=carousel_template)
                    line_bot_api.reply_message(tk, template_message)              
    else:
        # 如果用戶輸入其他文字，顯示主選單
        buttons_template = ButtonsTemplate(
            title="選擇項目", 
            text="請選擇你要查詢的項目", 
            actions=[MessageAction(label=menu_item, text=menu_item) for menu_item in main_menu.keys()]
        )
        template_message = TemplateSendMessage(alt_text="選擇項目", template=buttons_template)
        line_bot_api.reply_message(tk, template_message)

def location(latitude, longitude, user_id, tk):
    current_timestamp = int(time.time())
    with open('/tmp/'+user_id+'.txt', 'w') as f:
        f.write(f"{latitude},{longitude},{current_timestamp}\n")
    buttons_template = ButtonsTemplate(
        title="選擇項目", 
        text="請選擇你要查詢的項目", 
        actions=[MessageAction(label=menu_item, text=menu_item) for menu_item in main_menu.keys()]
    )
    template_message = TemplateSendMessage(alt_text="選擇項目", template=buttons_template)
    line_bot_api.reply_message(tk, template_message)

# -------- 行事曆 --------
USER_ID = os.getenv('USER_ID')

# 服務帳戶的日曆授權
def get_calendar_service():
    calendar_credentials_dict = {
        "type" :"service_account",
        "project_id": os.getenv("project_id"),
        "private_key_id": os.getenv("private_key_id"),
        "private_key": os.getenv("private_key").replace('\\n', '\n'),
        "client_email": os.getenv("client_email"),
        "client_id": os.getenv("client_id"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.getenv("client_x509_cert_url"),
        "universe_domain": "googleapis.com"
    }
    calendar_scopes = ['https://www.googleapis.com/auth/calendar']
    credentials = service_account.Credentials.from_service_account_info(
        calendar_credentials_dict, scopes=calendar_scopes)
    calendar_service = build('calendar', 'v3', credentials=credentials)
    return calendar_service
calendar_credentials = get_calendar_service()

# 新增事件
def add_event(summary, start_time, end_time, location=''):
    service = get_calendar_service()
    event = {
        'summary': summary,
        'location': location,
        'start': {'dateTime': start_time, 'timeZone': 'Asia/Taipei'},
        'end': {'dateTime': end_time, 'timeZone': 'Asia/Taipei'},
    }
    service.events().insert(calendarId='primary', body=event).execute()

# 刪除事件
def delete_event_by_keyword(keyword):
    service = get_calendar_service()
    now = datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(calendarId='primary', timeMin=now, maxResults=10).execute()
    for event in events_result.get('items', []):
        if keyword in event['summary']:
            service.events().delete(calendarId='primary', eventId=event['id']).execute()
            return True
    return False

# 查詢今天的事件
def get_today_events():
    service = get_calendar_service()
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0).isoformat() + '+08:00'
    end = now.replace(hour=23, minute=59, second=59).isoformat() + '+08:00'
    events_result = service.events().list(
        calendarId='primary',
        timeMin=start,
        timeMax=end,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    return events_result.get('items', [])

# 自然語言處理（NLU）來解析意圖
def parse_intent(text):
    if any(kw in text for kw in ['新增', '安排', '有個']):
        return 'add'
    elif any(kw in text for kw in ['刪', '取消', '不要']):
        return 'delete'
    elif any(kw in text for kw in ['查', '有什麼', '行程']):
        return 'query'
    else:
        return 'unknown'

# 提取時間
def extract_datetime(text):
    match = re.search(r'(\d{4}-\d{2}-\d{2})[ ]?(\d{2}:\d{2})?', text)
    if match:
        date_str = match.group(1)
        time_str = match.group(2) or '09:00'
        dt = datetime.strptime(f'{date_str} {time_str}', "%Y-%m-%d %H:%M")
        return dt
    elif '明天' in text:
        dt = datetime.now() + timedelta(days=1)
        return dt.replace(hour=9, minute=0)
    elif '今天' in text:
        dt = datetime.now()
        return dt.replace(hour=9, minute=0)
    return None

# 提取事件信息
def extract_event_info(text):
    dt = extract_datetime(text)
    if dt:
        title = text.split(str(dt.date()))[0].strip()
        return title, dt
    return text, None

# 定時推播行程
def daily_push():
    events = get_today_events()
    if not events:
        msg = "今天沒有安排行程喔～"
    else:
        msg = "今天行程：\n"
        for e in events:
            time = e['start'].get('dateTime', '')[11:16]
            msg += f"- {time} {e['summary']}\n"
    line_bot_api.push_message(USER_ID, TextSendMessage(text=msg))
    
def calender(tk, intent, text):
    if intent == 'add':
        title, dt = extract_event_info(text)
        if dt:
            keyword = text.replace('新增', '').replace('安排', '').strip()
            end = dt + timedelta(hours=1)
            add_event(title, dt.isoformat(), end.isoformat())
            reply = f"已新增行程：{keyword}，新增時間：{dt.strftime('%Y-%m-%d %H:%M')}"
        else:
            reply = "請提供正確的時間格式，例如：'我明天早上9點開會'"
    elif intent == 'query':
        events = get_today_events()
        if events:
            reply = "今天行程：\n" + '\n'.join([f"- {e['start']['dateTime'][11:16]} {e['summary']}" for e in events])
        else:
            reply = "今天沒有行程喔"
    elif intent == 'delete':
        keyword = text.replace('刪除', '').replace('取消', '').strip()
        result = delete_event_by_keyword(keyword)
        reply = f"已刪除「{keyword}」行程" if result else f"找不到包含「{keyword}」的行程"
    else:
        reply = "請說明你想做什麼，例如：\n- 幫我新增明天下午3點開會\n- 查一下今天有什麼行程\n- 取消跟某人的約"

    line_bot_api.reply_message(tk, TextSendMessage(text=reply))    


# 啟動排程
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(daily_push, 'cron', hour=8, minute=0)
    scheduler.start()

# -------- Receiving LINE Messages --------
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        line_handler.handle(body, signature)
    except:
        print("error, but still work.")
    return 'OK'

@line_handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    global last_msg, memlist, random_list, user_pdf_data
    msg = event.message.text
    tk = event.reply_token
    user_id = event.source.user_id
    result = msg.split()
    
    if msg == 'ChatPDF':
        line_bot_api.reply_message(tk, TextSendMessage(text='請上傳PDF檔案，或輸入問題來查詢已上傳的PDF內容。'))
        last_msg = "chatpdf"
    elif last_msg == "chatpdf" and msg != '關閉ChatPDF':
        # 假設 process_pdf_query 是 async 函數，需使用 asyncio 運行
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if user_id in user_pdf_data and user_pdf_data[user_id]:
            response = loop.run_until_complete(process_pdf_query(user_pdf_data[user_id], msg))
            line_bot_api.reply_message(tk, TextSendMessage(text=response))
        else:
            line_bot_api.reply_message(tk, TextSendMessage(text='請先上傳PDF檔案。'))
    
    # Existing intents
    elif msg == '抽籤':
        random_list.clear()
        line_bot_api.reply_message(tk, TextSendMessage(text='給我一些想法 -> 推薦清單\n清空清單 -> 清單重置\n\n直接輸入文字將加入抽選項目中\n選項都加入完後 輸入開始抽籤吧'))
        last_msg = "random"
    elif msg == '查詢天氣':
        line_bot_api.reply_message(tk, TextSendMessage(text='請傳送位置資訊以查詢天氣與空氣品質'))
        last_msg = "weather"
    elif msg == '翻譯':
        line_bot_api.reply_message(tk, TextSendMessage(text='翻譯功能啟用\n請輸入欲翻譯的文字:'))
        last_msg = "translator"
    elif msg == '記帳':
        line_bot_api.reply_message(tk, TextSendMessage(text='請輸入關鍵字來進行記帳操作\n- 我要記帳\n- 查詢\n- 查 {類別}\n- 查詢日期 YYYY-MM-DD\n- 查詢月 YYYY-MM\n- 查詢月類別 YYYY-MM {類別}'))
        last_msg = "money"
    elif msg == '關閉記帳功能':
        last_msg = ""
    elif msg == '查詢附近美食與景點':
        foodie(tk, user_id, result)
        last_msg = "foodie02"
    elif msg == '行事曆':
        line_bot_api.reply_message(tk, TextSendMessage(text='新增行程/刪除行程/查詢行程'))
        last_msg = "calender"
    elif msg == '關閉行事曆':
        last_msg = ""
    elif last_msg == "random":
        last_msg, memlist = randomone(tk, msg, last_msg, memlist)
    elif last_msg == "translator":
        chooseLen(tk, msg)
        last_msg = ""
    elif last_msg == "money":
        money(tk, msg, user_id)
    elif last_msg == "foodie02":
        foodie(tk, user_id, result)
    elif last_msg == "calender":
        intent = parse_intent(msg)
        calender(tk, intent, msg)

@line_handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    global last_msg
    tk = event.reply_token
    address = event.message.address.replace('台', '臺')
    latitude = event.message.latitude
    longitude = event.message.longitude
    user_id = event.source.user_id
    if last_msg == "foodie02":
        location(latitude, longitude, user_id, tk)
        last_msg = "foodie02"
    elif last_msg == "weather":
        line_bot_api.reply_message(tk, TextSendMessage(text=weather(address)))

@line_handler.add(MessageEvent, message=FileMessage)
def handle_file_message(event):
    global last_msg, user_pdf_data
    tk = event.reply_token
    user_id = event.source.user_id
    file_id = event.message.id  # 使用 message.id 獲取檔案 ID
    file_name = event.message.file_name
    
    if last_msg == "chatpdf" and file_name.lower().endswith('.pdf'):
        try:
            # 從 LINE 下載檔案內容
            file_content = line_bot_api.get_message_content(file_id)
            # 使用 tempfile 儲存 PDF
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                for chunk in file_content.iter_content():
                    temp_file.write(chunk)
                temp_file_path = temp_file.name
            
            # 提取 PDF 文字
            pdf_text = extract_pdf_text(temp_file_path)
            if pdf_text:
                user_pdf_data[user_id] = pdf_text
                line_bot_api.reply_message(tk, TextSendMessage(text='PDF已上傳並處理完成！請輸入問題來查詢PDF內容。'))
            else:
                line_bot_api.reply_message(tk, TextSendMessage(text='無法解析PDF內容，請檢查檔案是否有效。'))
            
            # 清理臨時檔案
            os.unlink(temp_file_path)
        except Exception as e:
            print(f"處理 PDF 時發生錯誤: {e}")
            line_bot_api.reply_message(tk, TextSendMessage(text=f'處理 PDF 失敗：{str(e)}'))
    else:
        line_bot_api.reply_message(tk, TextSendMessage(text='請在ChatPDF模式下上傳PDF檔案。'))

@line_handler.add(PostbackEvent)
def handle_postback(event):
    tk = event.reply_token
    postback_data = event.postback.data
    params = {}
    for param in postback_data.split("&"):
        key, value = param.split("=")
        params[key] = value
    user_input = params.get("text")
    language = params.get("lang")
    result = azure_translate(user_input, language)
    line_bot_api.reply_message(tk, [TextMessage(text=result if result else "No translation available")])

if __name__ == '__main__':
    start_scheduler()
    app.run()