import asyncio
import aiohttp
import json
import os
import hashlib
import chardet
import time
import re
import threading
import urllib.parse

# 全局统计
download_stats = {
    'success': 0,
    'failed': 0,
    'total': 0
}
stats_lock = threading.Lock()

def fetch_wayback_data():
    import requests
    url = "https://web.archive.org/web/timemap/json"
    params = {
        "url": "http://mikufans.cn/",
        "matchType": "prefix",
        "collapse": "urlkey",
        "output": "json",
        "fl": "original,mimetype,timestamp,endtimestamp,groupcount,uniqcount"
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        print("成功获取Wayback Machine数据")
        return data
    except Exception as e:
        print(f"获取数据失败: {e}")
        return None

def detect_and_fix_encoding(content):
    detected = chardet.detect(content)
    encoding = detected['encoding'] if detected['encoding'] else 'utf-8'
    confidence = detected['confidence']
    
    chinese_encodings = ['utf-8', 'gbk', 'gb2312', 'big5', 'gb18030']
    japanese_encodings = ['utf-8', 'shift_jis', 'euc-jp', 'iso-2022-jp']
    korean_encodings = ['utf-8', 'euc-kr', 'iso-2022-kr']
    
    all_encodings = list(dict.fromkeys(chinese_encodings + japanese_encodings + korean_encodings))
    
    if confidence > 0.7:
        try:
            decoded_content = content.decode(encoding)
            if re.search(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7a3]', decoded_content):
                return decoded_content, encoding
        except (UnicodeDecodeError, LookupError):
            pass
    
    for enc in all_encodings:
        try:
            decoded_content = content.decode(enc)
            if re.search(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7a3]', decoded_content):
                return decoded_content, enc
        except (UnicodeDecodeError, LookupError):
            continue
    
    try:
        return content.decode(encoding, errors='replace'), encoding
    except (UnicodeDecodeError, LookupError):
        return content.decode('utf-8', errors='ignore'), 'utf-8'

def extract_encoding_from_html(html_content):
    encoding_patterns = [
        r'<meta[^>]*charset=["\']?([a-zA-Z0-9-]+)["\']?',
        r'<meta[^>]*content=["\'][^"\']*charset=([a-zA-Z0-9-]+)',
    ]
    for pattern in encoding_patterns:
        match = re.search(pattern, html_content, re.IGNORECASE)
        if match:
            encoding = match.group(1).lower()
            encoding_aliases = {
                'shift-jis': 'shift_jis',
                'x-sjis': 'shift_jis',
                'windows-31j': 'shift_jis',
                'cp932': 'shift_jis',
                'ms932': 'shift_jis',
                'euc-jp': 'euc_jp',
                'x-euc-jp': 'euc_jp',
                'euc-kr': 'euc_kr',
                'ks_c_5601-1987': 'euc_kr',
                'cp949': 'euc_kr',
            }
            return encoding_aliases.get(encoding, encoding)
    return None

def detect_language(content):
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', content))
    japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', content))
    korean_chars = len(re.findall(r'[\uac00-\ud7a3]', content))
    if japanese_chars > chinese_chars and japanese_chars > korean_chars:
        return 'japanese'
    elif korean_chars > chinese_chars and korean_chars > japanese_chars:
        return 'korean'
    elif chinese_chars > 0:
        return 'chinese'
    else:
        return 'unknown'

async def download_snapshot(session, snapshot, index, total):
    original_url = snapshot[0]
    timestamp = snapshot[2]
    wayback_url = f"https://web.archive.org/web/{timestamp}if_/{original_url}"
    
    # 解析原始URL的路径，构造保存路径
    parsed = urllib.parse.urlparse(original_url)
    path = parsed.path
    # 去除开头的 '/'
    if path.startswith('/'):
        path = path[1:]
    # 如果路径为空或只有根，使用 index.html
    if not path:
        path = 'index.html'
    # 替换文件系统中的非法字符（简单处理）
    safe_path = re.sub(r'[<>:"|?*]', '_', path)
    
    # 构建保存目录和文件路径：wayback_downloads/时间戳/路径
    base_dir = "wayback_downloads"
    save_dir = os.path.join(base_dir, timestamp)
    full_path = os.path.join(save_dir, safe_path)
    
    # 确保目录存在
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate',
    }
    
    try:
        async with session.get(wayback_url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as response:
            response.raise_for_status()
            content_bytes = await response.read()
        
        decoded_content, used_encoding = detect_and_fix_encoding(content_bytes)
        language = detect_language(decoded_content)
        if language == 'japanese':
            print(f"检测到日语内容 ({index}/{total})，使用编码: {used_encoding}")
        
        html_encoding = extract_encoding_from_html(decoded_content)
        if html_encoding and html_encoding != used_encoding:
            try:
                decoded_content = content_bytes.decode(html_encoding)
                used_encoding = html_encoding
                if language == 'japanese':
                    print(f"使用HTML声明的编码: {html_encoding}")
            except (UnicodeDecodeError, LookupError):
                pass
        
        # 添加meta charset（如果需要）
        if '<meta charset=' not in decoded_content and 'charset=' not in decoded_content:
            if '<head>' in decoded_content:
                decoded_content = decoded_content.replace('<head>', f'<head><meta charset="{used_encoding}">')
            else:
                decoded_content = decoded_content.replace('<html>', f'<html><head><meta charset="{used_encoding}"></head>', 1)
        
        # 写入文件
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(decoded_content)
        
        with stats_lock:
            download_stats['success'] += 1
        
        return f"成功下载 ({index}/{total}): {full_path} (语言: {language}, 编码: {used_encoding})"
    
    except Exception as e:
        with stats_lock:
            download_stats['failed'] += 1
        return f"下载失败 ({index}/{total}): {wayback_url}, 错误: {e}"

async def download_all_snapshots_async(snapshots, max_concurrent=10):
    os.makedirs("wayback_downloads", exist_ok=True)
    print(f"开始异步下载 {len(snapshots)} 个快照，并发数: {max_concurrent}")
    
    connector = aiohttp.TCPConnector(limit=max_concurrent, limit_per_host=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def bounded_download(snapshot, idx, total):
            async with semaphore:
                return await download_snapshot(session, snapshot, idx, total)
        
        tasks = [bounded_download(snapshot, i+1, len(snapshots)) for i, snapshot in enumerate(snapshots)]
        
        for coro in asyncio.as_completed(tasks):
            result = await coro
            print(result)
            
            if (download_stats['success'] + download_stats['failed']) % 10 == 0:
                success = download_stats['success']
                failed = download_stats['failed']
                total = len(snapshots)
                print(f"进度: {success + failed}/{total} (成功: {success}, 失败: {failed})")

def main():
    print("开始获取Wayback Machine数据...")
    data = fetch_wayback_data()
    if not data:
        print("未能获取数据，程序退出。")
        return
    
    cutoff_timestamp = "20100110000000"
    snapshots = data[1:]
    filtered_snapshots = [s for s in snapshots if s[2] < cutoff_timestamp]
    print(f"找到 {len(filtered_snapshots)} 个符合条件的快照")
    
    download_stats['total'] = len(filtered_snapshots)
    download_stats['success'] = 0
    download_stats['failed'] = 0
    
    start_time = time.time()
    
    batch_size = 100
    for i in range(0, len(filtered_snapshots), batch_size):
        batch = filtered_snapshots[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(filtered_snapshots) - 1) // batch_size + 1
        print(f"\n正在下载批次 {batch_num}/{total_batches} (共 {len(batch)} 个文件)")
        
        asyncio.run(download_all_snapshots_async(batch, max_concurrent=10))
        
        if i + batch_size < len(filtered_snapshots):
            wait_time = 5
            print(f"批次完成，等待 {wait_time} 秒后继续下一批次...")
            time.sleep(wait_time)
    
    end_time = time.time()
    elapsed_time = end_time - start_time
    success = download_stats['success']
    failed = download_stats['failed']
    total = download_stats['total']
    
    print(f"\n所有任务处理完成！")
    print(f"总耗时: {elapsed_time:.2f} 秒")
    print(f"平均速度: {success/elapsed_time:.2f} 个/秒")
    print(f"最终结果: {success} 成功, {failed} 失败")
    
    with open("download_stats.txt", "w") as f:
        f.write(f"总任务数: {total}\n")
        f.write(f"成功: {success}\n")
        f.write(f"失败: {failed}\n")
        f.write(f"成功率: {success/total*100:.2f}%\n")
        f.write(f"总耗时: {elapsed_time:.2f} 秒\n")
        f.write(f"平均速度: {success/elapsed_time:.2f} 个/秒\n")

if __name__ == "__main__":
    print("开始执行Wayback Machine异步下载任务...")
    main()
