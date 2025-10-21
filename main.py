# -*- coding: utf-8 -*-

import requests
from bs4 import BeautifulSoup
import sqlalchemy
from sqlalchemy import text
import os
import json
import time
import re
from collections import deque
from urllib.parse import urljoin, urlparse, unquote # <-- ДОБАВЛЕН unquote

# Импорты для красивого интерфейса
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live
from rich.spinner import Spinner
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, MofNCompleteColumn
from rich.layout import Layout

# ==============================================================================
# --- КОНФИГУРАЦИЯ И РАБОТА С НАСТРОЙКАМИ ---
# ==============================================================================

console = Console()
SETTINGS_FILE = "settings.json"

def load_settings():
    """Загружает настройки из файла, создавая его при необходимости."""
    default_settings = {
        "gemini_api_key": "",
        "crawler_start_urls": [
            "https://wiki.ss14.su/view/Заглавная_страница",
            "https://wiki.deadspace14.net/Заглавная_страница"
        ],
        "max_pages_per_crawl": 50,
        "current_server_context": "all"
    }
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                user_settings = json.load(f)
                for key, value in default_settings.items():
                    user_settings.setdefault(key, value)
                return user_settings
        else:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_settings, f, indent=4, ensure_ascii=False)
            console.print(f"[yellow]Файл настроек '{SETTINGS_FILE}' создан с параметрами по умолчанию.[/yellow]")
            return default_settings
    except Exception as e:
        console.print(f"[bold red]Ошибка чтения файла настроек: {e}. Будут использованы настройки по умолчанию.[/bold red]")
        return default_settings

def save_settings():
    """Сохраняет текущие настройки в файл."""
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(SETTINGS, f, indent=4, ensure_ascii=False)
        console.print("[bold green]Настройки успешно сохранены.[/bold green]")
    except IOError as e:
        console.print(f"[bold red]Не удалось сохранить настройки: {e}[/bold red]")

SETTINGS = load_settings()
VERCEL_PROXY_URL = "https://my-game-proxy.vercel.app/api/proxy"
DB_NAME = 'ss14_data.db'
engine = sqlalchemy.create_engine(f'sqlite:///{DB_NAME}')
metadata = sqlalchemy.MetaData()

# ==============================================================================
# --- МЕНЮ НАСТРОЕК И КОНТЕКСТА ---
# ==============================================================================

def manage_server_context():
    urls = SETTINGS.get("crawler_start_urls", [])
    options_text = "[bold]Выберите контекст для поиска[/bold]\n0. [cyan]Общий (искать по всем источникам)[/cyan]\n"
    options_text += "\n".join([f"{i+1}. [cyan]{urlparse(url).netloc}[/cyan]" for i, url in enumerate(urls)])
    console.print(Panel(options_text, title="[yellow]Контекст сервера[/yellow]", border_style="blue"))
    choices = [str(i) for i in range(len(urls) + 1)]
    choice = int(Prompt.ask("Выберите номер", choices=choices, default="0"))
    if choice == 0:
        SETTINGS["current_server_context"] = "all"
        console.print("Контекст установлен на [bold green]Общий[/bold green].")
    else:
        selected_url = urls[choice - 1]
        SETTINGS["current_server_context"] = selected_url
        console.print(f"Контекст установлен на [bold green]{urlparse(selected_url).netloc}[/bold green].")
    save_settings()

def _settings_api_key():
    current_key = SETTINGS.get("gemini_api_key", "")
    masked_key = f"{current_key[:4]}...{current_key[-4:]}" if len(current_key) > 8 else "Не задан"
    console.print(f"Текущий API ключ: [cyan]{masked_key}[/cyan]")
    new_key = Prompt.ask("[yellow]Введите новый API ключ (оставьте пустым, чтобы отменить)[/yellow]", default=current_key)
    if new_key != current_key:
        SETTINGS["gemini_api_key"] = new_key
        save_settings()

def _settings_crawler_urls():
    while True:
        console.print("\n[bold]Текущие стартовые страницы для сканирования:[/bold]")
        urls = SETTINGS.get("crawler_start_urls", [])
        if not urls: console.print("[italic]Список пуст.[/italic]")
        else:
            for i, url in enumerate(urls): console.print(f"  [cyan]{i + 1}[/cyan]: {url}")
        console.print("\n[yellow]Команды:[/yellow] [bold]добавить[/bold] [italic]<url>[/italic], [bold]удалить[/bold] [italic]<номер>[/italic], [bold]назад[/bold]")
        command = Prompt.ask("Введите команду")
        if command.lower() == 'назад': break
        elif command.lower().startswith('добавить '):
            new_url = command[9:].strip()
            if new_url: urls.append(new_url); save_settings()
            else: console.print("[red]URL не может быть пустым.[/red]")
        elif command.lower().startswith('удалить '):
            try:
                index = int(command[8:].strip()) - 1
                if 0 <= index < len(urls):
                    removed_url = urls.pop(index)
                    console.print(f"URL '{removed_url}' удален."); save_settings()
                else: console.print("[red]Неверный номер.[/red]")
            except ValueError: console.print("[red]Пожалуйста, введите корректный номер.[/red]")
        else: console.print("[red]Неизвестная команда.[/red]")

def _settings_max_pages():
    current_value = SETTINGS.get("max_pages_per_crawl", 50)
    console.print(f"Текущая глубина сканирования: [cyan]{current_value}[/cyan] страниц за сайт.")
    try:
        new_value = int(Prompt.ask(f"[yellow]Введите новое значение (1-500)[/yellow]", default=str(current_value)))
        if 1 <= new_value <= 500:
            if new_value != current_value: SETTINGS["max_pages_per_crawl"] = new_value; save_settings()
        else: console.print("[red]Значение должно быть в диапазоне от 1 до 500.[/red]")
    except ValueError: console.print("[red]Пожалуйста, введите число.[/red]")

def manage_settings():
    while True:
        console.print(Panel("""
[bold]Меню настроек[/bold]
1. [cyan]Изменить API ключ Gemini[/cyan]
2. [cyan]Настроить список сайтов для сканирования[/cyan]
3. [cyan]Изменить глубину сканирования[/cyan]
4. [yellow]Вернуться в главное меню[/yellow]
        """, border_style="blue"))
        choice = Prompt.ask("Выберите пункт меню", choices=["1", "2", "3", "4"], default="4")
        if choice == "1": _settings_api_key()
        elif choice == "2": _settings_crawler_urls()
        elif choice == "3": _settings_max_pages()
        elif choice == "4": break

# ==============================================================================
# --- ОПРЕДЕЛЕНИЕ СТРУКТУРЫ БАЗЫ ДАННЫХ ---
# ==============================================================================

servers_table = sqlalchemy.Table('servers', metadata, sqlalchemy.Column('id', sqlalchemy.Integer, primary_key=True), sqlalchemy.Column('name', sqlalchemy.String), sqlalchemy.Column('address', sqlalchemy.String, unique=True), sqlalchemy.Column('players_online', sqlalchemy.Integer), sqlalchemy.Column('last_seen', sqlalchemy.DateTime, default=sqlalchemy.func.now(), onupdate=sqlalchemy.func.now()))
wiki_articles_table = sqlalchemy.Table('wiki_articles', metadata, sqlalchemy.Column('id', sqlalchemy.Integer, primary_key=True), sqlalchemy.Column('title', sqlalchemy.String), sqlalchemy.Column('content', sqlalchemy.Text), sqlalchemy.Column('source', sqlalchemy.String, unique=True), sqlalchemy.Column('last_updated', sqlalchemy.DateTime, default=sqlalchemy.func.now(), onupdate=sqlalchemy.func.now()))

# ==============================================================================
# --- МОДУЛЬ СБОРА ДАННЫХ ---
# ==============================================================================

def setup_database():
    metadata.create_all(engine)

def truncate_text(text, max_length=50):
    """Обрезает текст, если он слишком длинный."""
    if len(text) > max_length:
        return text[:max_length-3] + "..."
    return text

def create_layout() -> Layout:
    """Создает статичную структуру интерфейса."""
    layout = Layout(name="root")
    layout.split(
        Layout(Panel("SS14 Helper - Обновление Базы", style="bold blue"), name="header", size=3),
        Layout(name="main"),
    )
    return layout

def fetch_servers_with_progress(progress, task_id):
    progress.update(task_id, description="[cyan]Получение списка серверов...[/cyan]")
    try:
        response = requests.get('https://central.spacestation14.io/hub/api/servers', timeout=15); response.raise_for_status(); servers_data = response.json()
        with engine.connect() as connection:
            trans = connection.begin()
            for server in servers_data:
                server_address = server.get('address')
                if not server_address: continue
                server_name = server.get('name', f"Безымянный сервер ({server_address[:20]}...)"); server_players = server.get('players', 0)
                update_stmt = sqlalchemy.update(servers_table).where(servers_table.c.address == server_address).values(name=server_name, players_online=server_players)
                result = connection.execute(update_stmt)
                if result.rowcount == 0:
                    insert_stmt = sqlalchemy.insert(servers_table).values(name=server_name, address=server_address, players_online=server_players)
                    connection.execute(insert_stmt)
            trans.commit()
        progress.update(task_id, completed=1, description=f"[green]Список серверов обновлен ({len(servers_data)} шт.)[/green]")
    except Exception as e:
        progress.update(task_id, description=f"[red]Ошибка получения серверов: {e}[/red]")

def scrape_and_find_links(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (My SS14 Helper Bot)'}; response = requests.get(url, timeout=10, headers=headers); response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser'); title_element = soup.find(id='firstHeading'); content_div = soup.find('div', class_='mw-parser-output')
        if not title_element or not content_div: return None, []
        title = title_element.get_text(); content = content_div.get_text(separator='\n', strip=True)
        with engine.connect() as connection:
            trans = connection.begin()
            stmt = sqlalchemy.dialects.sqlite.insert(wiki_articles_table).values(title=title, content=content, source=url).on_conflict_do_update(index_elements=['source'], set_=dict(title=title, content=content))
            connection.execute(stmt); trans.commit()
        return title, [link['href'] for link in content_div.find_all('a', href=True)]
    except requests.RequestException:
        return None, []

def run_crawler_with_progress(progress, task_id, start_url):
    max_pages = SETTINGS.get("max_pages_per_crawl", 50)
    base_netloc = urlparse(start_url).netloc
    pages_to_crawl = deque([start_url])
    crawled_pages = set()
    pages_count = 0
    while pages_to_crawl and not progress.tasks[task_id].finished:
        current_url = pages_to_crawl.popleft()
        if current_url in crawled_pages: continue
        
        # --- ИСПРАВЛЕНИЕ 1: Декодируем URL перед отображением ---
        decoded_url_part = unquote(current_url.split('/')[-1])
        short_url = truncate_text(decoded_url_part, 40)
        progress.update(task_id, description=f"[cyan]Анализ {base_netloc}:[/cyan] {short_url}")
        
        crawled_pages.add(current_url)
        title, found_links = scrape_and_find_links(current_url)
        if title:
            pages_count += 1
            progress.advance(task_id)
        for href in found_links:
            full_url = urljoin(current_url, href).split('#')[0]
            if urlparse(full_url).netloc != base_netloc: continue
            if any(kw in full_url for kw in [':Special:', ':File:', '.png', '.jpg', 'action=edit']): continue
            if full_url not in crawled_pages and full_url not in pages_to_crawl:
                pages_to_crawl.append(full_url)
        time.sleep(0.1)

    # --- ИСПРАВЛЕНИЕ 2: Завершаем прогресс-бар и показываем реальное количество ---
    progress.update(task_id, completed=max_pages, description=f"[green]Анализ {base_netloc} завершен ({pages_count} стр.)[/green]")

def autonomous_update():
    setup_database()
    layout = create_layout()
    progress = Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}", justify="left"), BarColumn(bar_width=None), MofNCompleteColumn(), TimeElapsedColumn(), console=console)
    layout["main"].update(Panel(progress, title="[yellow]Процесс обновления[/yellow]", border_style="green"))
    with Live(layout, screen=True, redirect_stderr=False, vertical_overflow="visible") as live:
        start_points = SETTINGS.get("crawler_start_urls", [])
        if not start_points:
            console.print("[bold red]Ошибка:[/bold red] Список сайтов для сканирования пуст. Зайдите в настройки и добавьте URL.")
            time.sleep(3); return
        overall_task = progress.add_task("[bold]Общий прогресс[/bold]", total=len(start_points) + 1)
        server_task = progress.add_task("Серверы", total=1)
        fetch_servers_with_progress(progress, server_task)
        progress.advance(overall_task)
        for url in start_points:
            max_pages = SETTINGS.get("max_pages_per_crawl", 50)
            crawl_task = progress.add_task(f"Сайт: {urlparse(url).netloc}", total=max_pages)
            run_crawler_with_progress(progress, crawl_task, url)
            progress.advance(overall_task)
        progress.update(overall_task, description="[bold green]Обновление завершено![/bold green]")
        time.sleep(2)

# ==============================================================================
# --- МОДУЛЬ ВЗАИМОДЕЙСТВИЯ С GEMINI AI ---
# ==============================================================================

def find_relevant_context(keywords, server_context="all"):
    if not keywords: return ""
    context = ""
    with engine.connect() as connection:
        search_conditions = " OR ".join([f"content LIKE :kw{i}" for i in range(len(keywords))])
        params = {f"kw{i}": f"%{keyword}%" for i, keyword in enumerate(keywords)}
        query_str = f"SELECT title, content FROM wiki_articles WHERE ({search_conditions})"
        if server_context != "all":
            base_url = urlparse(server_context).netloc
            query_str += " AND source LIKE :server_url"
            params["server_url"] = f"%{base_url}%"
        query_str += " LIMIT 5"
        stmt = text(query_str)
        results = connection.execute(stmt, params).fetchall()
        for row in results:
            context += f"## Статья: {row[0]}\n\n{row[1]}\n\n---\n\n"
    return context[:20000]

def get_refined_search_keywords_from_gemini(query):
    api_key = SETTINGS.get("gemini_api_key", "")
    prompt = f"""Проанализируй запрос пользователя об игре Space Station 14. Преврати его в список ключевых слов или фраз для поиска по базе данных. Выведи только ключевые слова через запятую.
Запрос пользователя: "{query}"
Ответ:"""
    payload = { "contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.0} }
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
    try:
        response = requests.post(VERCEL_PROXY_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        refined_keywords_text = data['candidates'][0]['content']['parts'][0]['text']
        return [kw.strip() for kw in refined_keywords_text.split(',')]
    except Exception:
        return []

def ask_gemini(query):
    api_key = SETTINGS.get("gemini_api_key", "")
    if not api_key:
        return "[bold red]Ошибка:[/bold red] API ключ не задан. Пожалуйста, введите его в меню 'настройки'."
    answer = ""
    with Live(Spinner('dots', text="[cyan]Анализирую запрос...[/cyan]"), auto_refresh=True, transient=True) as live:
        current_context_url = SETTINGS.get("current_server_context", "all")
        context_name = urlparse(current_context_url).netloc if current_context_url != "all" else "Общий"
        live.update(Spinner('dots', text=f"[cyan]Ищу в контексте '{context_name}'...[/cyan]"))
        initial_keywords = re.findall(r'\b\w+\b', query.lower())
        context = find_relevant_context(initial_keywords, server_context=current_context_url)
        if (not context or len(context) < 200) and current_context_url != "all":
            live.update(Spinner('dots', text=f"[cyan]В '{context_name}' не найдено. Ищу по всем источникам...[/cyan]"))
            context = find_relevant_context(initial_keywords, server_context="all")
        if not context or len(context) < 200:
            live.update(Spinner('dots', text="[cyan]Уточняю запрос с помощью ИИ...[/cyan]"))
            refined_keywords = get_refined_search_keywords_from_gemini(query)
            if refined_keywords:
                live.update(Spinner('dots', text="[cyan]Ищу по уточненным словам...[/cyan]"))
                context = find_relevant_context(refined_keywords, server_context="all")
        if not context:
            live.update(Spinner('dots', text="[cyan]Локально не найдено. Спрашиваю Gemini с контекстом игры...[/cyan]"))
            prompt = f"Ты — эксперт-помощник по игре Space Station 14. Ответь на следующий вопрос, касающийся этой игры: '{query}'"
        else:
            live.update(Spinner('dots', text="[cyan]Информация найдена. Формирую точный запрос для Gemini...[/cyan]"))
            prompt = f"""Ты — эксперт-помощник по игре Space Station 14. Используя ТОЛЬКО предоставленный ниже контекст из игровой вики, дай подробный ответ на вопрос.
Контекст:
---
{context}
---
Вопрос: {query}"""
        payload = { "contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.5}, "safetySettings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"}, {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"}, {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"}, {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"}] }
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
        try:
            live.update(Spinner('dots', text="[cyan]Отправляю финальный запрос на прокси-сервер...[/cyan]"))
            response = requests.post(VERCEL_PROXY_URL, json=payload, headers=headers, timeout=90)
            response.raise_for_status()
            data = response.json()
            answer = data['candidates'][0]['content']['parts'][0]['text']
        except requests.exceptions.HTTPError as e:
            answer = f"[bold red]Ошибка HTTP:[/bold red] {e.response.status_code}. Ответ сервера: {e.response.text}"
        except Exception as e:
            answer = f"[bold red]Произошла непредвиденная ошибка:[/bold red] {e}"
    return answer

# ==============================================================================
# --- ОСНОВНОЙ ЦИКЛ ПРОГРАММЫ ---
# ==============================================================================

if __name__ == '__main__':
    welcome_message = """
[bold]Привет! Я твой помощник по Space Station 14.[/bold]

- Введи '[bold]обновить[/bold]' для загрузки свежих данных.
- Введи '[bold]настройки[/bold]' для изменения параметров.
- Введи '[bold]сервер[/bold]' для смены контекста поиска.
- Введи '[bold]выход[/bold]' для завершения.
    """
    console.print(Panel(welcome_message, title="[yellow]SS14 Helper[/yellow]", border_style="blue"))

    while True:
        context_url = SETTINGS.get("current_server_context", "all")
        context_display = f"[bold green]Общий[/bold green]" if context_url == "all" else f"[bold cyan]{urlparse(context_url).netloc}[/bold cyan]"
        prompt_text = f"\n[bold yellow]Твой вопрос (Контекст: {context_display})[/bold yellow]"
        user_input = Prompt.ask(prompt_text)
        
        if user_input.lower() == 'выход':
            console.print("[bold blue]Удачной смены![/bold blue]")
            break
        elif user_input.lower() == 'обновить':
            autonomous_update()
        elif user_input.lower() == 'настройки':
            manage_settings()
        elif user_input.lower() in ['сервер', 'контекст']:
            manage_server_context()
        else:
            answer = ask_gemini(user_input)
            console.print(Panel(Markdown(answer), title="[green]Ответ Gemini[/green]", border_style="green", padding=(1, 2)))