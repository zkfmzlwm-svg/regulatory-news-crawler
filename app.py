import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from tkinter import BooleanVar, StringVar, Tk, Toplevel, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from src import __version__
from src.crawler import RECENT_DAYS, crawl_all, format_report, load_sources, save_sources
from src.fetcher import fetch_full_text, fetch_page
from src.models import Article
from src.paths import FORMAT_CONFIG, OUTPUT_DIR, SOURCES_CONFIG, ensure_default_config
from src.storage import DEFAULT_DB_PATH, Storage
from src.summarizer import (
    SummaryEntry,
    load_format,
    output_extension,
    render_txt,
    summarize_article,
    write_summaries,
)
from src.utils import clean_text, local_date

UI_FONT = ("Malgun Gothic", 10)
LIST_LIMIT = 300
ALL_TAGS = "전체"
NEW_ROW_COLOR = "#fff4c2"


class App(Tk):
    def __init__(self):
        super().__init__()
        self.title(f"해외 의약품 규제 뉴스 크롤러 v{__version__}")
        self.geometry("1120x760")
        self.minsize(900, 600)

        ensure_default_config()
        self.store = Storage(DEFAULT_DB_PATH)
        self.articles = []
        self.total_count = 0
        self.selected_ids = set()
        self.last_output_path = None
        self.new_since = None  # 이번 실행에서 마지막 수집을 시작한 시각 (그 뒤로 저장된 기사 = 신규)
        self.task_queue = queue.Queue()

        self._build_ui()
        self.refresh_list()
        self.after(100, self._poll_queue)

    # ---------------- UI 구성 ----------------
    def _build_ui(self):
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")

        self.crawl_btn = ttk.Button(toolbar, text="🔄 기사 수집", command=self.start_crawl)
        self.crawl_btn.pack(side="left", padx=(0, 12))

        ttk.Label(toolbar, text="키워드").pack(side="left")
        self.keyword_var = StringVar()
        keyword_entry = ttk.Entry(toolbar, textvariable=self.keyword_var, width=16)
        keyword_entry.pack(side="left", padx=(4, 8))
        keyword_entry.bind("<Return>", lambda e: self.refresh_list())

        ttk.Label(toolbar, text="출처").pack(side="left")
        self.tag_var = StringVar(value=ALL_TAGS)
        self.tag_combo = ttk.Combobox(toolbar, textvariable=self.tag_var, state="readonly", width=13)
        self.tag_combo.pack(side="left", padx=(4, 8))
        self.tag_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_list())

        self.unsummarized_only_var = BooleanVar()
        ttk.Checkbutton(
            toolbar,
            text="요약 안 한 것만",
            variable=self.unsummarized_only_var,
            command=self.refresh_list,
        ).pack(side="left", padx=(0, 8))

        self.new_only_var = BooleanVar()
        self.new_only_check = ttk.Checkbutton(
            toolbar, text="신규만", variable=self.new_only_var, command=self.refresh_list, state="disabled"
        )
        self.new_only_check.pack(side="left", padx=(0, 8))

        ttk.Button(toolbar, text="검색", command=self.refresh_list).pack(side="left")
        ttk.Button(toolbar, text="사이트 관리", command=self.open_source_manager).pack(side="right")

        url_bar = ttk.Frame(self, padding=(8, 0, 8, 6))
        url_bar.pack(fill="x")
        ttk.Label(url_bar, text="웹페이지 주소").pack(side="left")
        self.url_buttons = [
            ttk.Button(url_bar, text="🌍 이 페이지 번역 요약", command=lambda: self.start_url_summarize("free")),
            ttk.Button(url_bar, text="🆓 이 페이지 요약 (영어)", command=lambda: self.start_url_summarize("simple")),
        ]
        for btn in self.url_buttons:
            btn.pack(side="right", padx=(4, 0))
        self.url_var = StringVar()
        url_entry = ttk.Entry(url_bar, textvariable=self.url_var)
        url_entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        url_entry.bind("<Return>", lambda e: self.start_url_summarize("free"))

        hint = ttk.Label(
            self,
            text="※ '선택' 칸 클릭 = 선택/해제 ('선택' 제목 = 화면 전체), 제목 더블클릭 = 원문 열기, 노란 줄 = 방금 수집된 신규 기사",
            padding=(8, 0),
            foreground="#555555",
        )
        hint.pack(fill="x")

        # 창이 작아도 상태 표시줄이 밀려나지 않도록 목록보다 먼저 아래쪽에 붙인다
        self.status_var = StringVar(value="준비됨")
        ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(8, 4)).pack(side="bottom", fill="x")

        # 기사 목록과 결과창 사이 경계선을 끌어서 크기를 조절할 수 있게 한다
        panes = ttk.Panedwindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=8, pady=(4, 0))

        list_pane = ttk.Frame(panes)
        # 선택 건수·요약 버튼 줄은 창이 작아져도 잘리지 않도록 표보다 먼저 아래쪽에 붙인다
        action_frame = ttk.Frame(list_pane, padding=(0, 6))
        action_frame.pack(side="bottom", fill="x")
        list_frame = ttk.Frame(list_pane)
        list_frame.pack(fill="both", expand=True)

        columns = ("select", "date", "source", "title", "summarized")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="none", height=5)
        headers = {"select": "선택", "date": "날짜", "source": "출처", "title": "제목", "summarized": "요약됨"}
        widths = {"select": 50, "date": 90, "source": 110, "title": 620, "summarized": 60}
        anchors = {"select": "center", "date": "center", "source": "w", "title": "w", "summarized": "center"}
        stretch = {"title": True}
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], anchor=anchors[col], stretch=stretch.get(col, False))
        self.tree.heading("select", command=self.toggle_all_visible)
        self.tree.tag_configure("new", background=NEW_ROW_COLOR)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        self.selection_label = ttk.Label(action_frame, text="0건 표시 · 0건 선택됨")
        self.selection_label.pack(side="left")
        ttk.Button(action_frame, text="선택 모두 해제", command=self.clear_selection).pack(side="left", padx=(12, 0))

        self.summary_buttons = [
            ttk.Button(action_frame, text="🌍 번역 요약 (무료)", command=lambda: self.start_summarize("free")),
            ttk.Button(action_frame, text="🆓 단순 요약 (영어)", command=lambda: self.start_summarize("simple")),
        ]
        for btn in self.summary_buttons:
            btn.pack(side="right", padx=4)
        panes.add(list_pane, weight=3)

        result_frame = ttk.LabelFrame(panes, text="결과", padding=8)
        self.result_text = ScrolledText(result_frame, height=5, wrap="word", font=UI_FONT)
        self.result_text.pack(fill="both", expand=True)

        save_row = ttk.Frame(result_frame)
        save_row.pack(fill="x", pady=(6, 0))
        ttk.Button(save_row, text="📋 결과 복사", command=self.copy_result).pack(side="left")
        ttk.Button(save_row, text="📁 다른 이름으로 저장", command=self.save_as).pack(side="left", padx=(6, 0))
        ttk.Button(save_row, text="📂 저장 폴더 열기", command=self.open_output_dir).pack(side="left", padx=(6, 0))
        panes.add(result_frame, weight=1)
        # 처음에는 기사 목록을 넓게 (경계선은 마우스로 끌어 조절 가능)
        self.after_idle(lambda: panes.sashpos(0, int(panes.winfo_height() * 0.62)))

    # ---------------- 기사 목록 ----------------
    def refresh_list(self):
        keyword = self.keyword_var.get().strip() or None
        tag = self.tag_var.get()
        self.articles = self.store.list_articles(
            keyword=keyword,
            tag=None if tag == ALL_TAGS else tag,
            summarized=False if self.unsummarized_only_var.get() else None,
            collected_since=self.new_since if self.new_only_var.get() else None,
            limit=LIST_LIMIT,
        )
        self.total_count = self.store.count_articles()
        self.tag_combo["values"] = [ALL_TAGS] + self.store.list_tags()
        # 검색어·출처를 바꿔도 앞에서 고른 기사는 계속 선택된 상태로 둔다 (여러 번 검색해 모아서 요약)
        self._render_tree()

    def _render_tree(self):
        self.tree.delete(*self.tree.get_children())
        for a in self.articles:
            mark = "☑" if a.id in self.selected_ids else "☐"
            done = "✅" if a.summarized else ""
            is_new = self.new_since is not None and a.collected_at >= self.new_since
            self.tree.insert(
                "",
                "end",
                iid=str(a.id),
                values=(mark, local_date(a.published_at), a.tag or a.source, clean_text(a.title), done),
                tags=("new",) if is_new else (),
            )
        self._update_selection_label()

    def _update_selection_label(self):
        shown = len(self.articles)
        text = f"{shown}건 표시"
        if shown >= LIST_LIMIT and self.total_count > shown:
            text += f" (최근 {LIST_LIMIT}건만)"
        text += f" · {len(self.selected_ids)}건 선택됨"
        hidden = len(self.selected_ids - {a.id for a in self.articles})
        if hidden:
            text += f" (목록에 안 보이는 {hidden}건 포함)"
        self.selection_label.config(text=text)

    def _set_selected(self, aid: int, selected: bool):
        if selected:
            self.selected_ids.add(aid)
        else:
            self.selected_ids.discard(aid)
        if self.tree.exists(str(aid)):
            self.tree.set(str(aid), "select", "☑" if selected else "☐")

    def toggle_all_visible(self):
        """'선택' 제목 클릭: 화면에 보이는 기사를 모두 선택 (이미 모두 선택돼 있으면 모두 해제)."""
        visible = [a.id for a in self.articles]
        select = not all(aid in self.selected_ids for aid in visible)
        for aid in visible:
            self._set_selected(aid, select)
        self._update_selection_label()

    def clear_selection(self):
        for aid in list(self.selected_ids):
            self._set_selected(aid, False)
        self._update_selection_label()

    def _on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        aid = int(row_id)
        self._set_selected(aid, aid not in self.selected_ids)
        self._update_selection_label()

    def _on_tree_double_click(self, event):
        # '선택' 칸을 빠르게 두 번 누른 건 원문 열기가 아니라 체크를 한 번 더 누른 것
        if self.tree.identify_column(event.x) == "#1":
            self._on_tree_click(event)
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        article = next((a for a in self.articles if a.id == int(row_id)), None)
        if article:
            webbrowser.open(article.url)

    # ---------------- 기사 수집 ----------------
    def start_crawl(self):
        self.crawl_btn.config(state="disabled", text="수집 중...")
        self.new_since = datetime.now(timezone.utc).isoformat()
        self.status_var.set(f"등록된 사이트에서 최근 {RECENT_DAYS}일 이내 기사를 가져오는 중...")
        threading.Thread(target=self._crawl_worker, daemon=True).start()

    def _crawl_worker(self):
        try:
            sources = load_sources(str(SOURCES_CONFIG))
            report = []
            found = crawl_all(
                sources,
                report=report,
                progress=lambda i, n, name: self.task_queue.put(("crawl_progress", (i, n, name))),
                last_seen=self.store.latest_published_by_source(),
            )
            added = self.store.add_articles(found)
            self.task_queue.put(("crawl_done", (len(found), added, report)))
        except Exception as exc:
            self.task_queue.put(("crawl_error", str(exc)))

    # ---------------- 요약 ----------------
    def _set_summarizing(self, busy: bool):
        for btn in self.summary_buttons + self.url_buttons:
            btn.config(state="disabled" if busy else "normal")

    def start_summarize(self, mode: str):
        if not self.selected_ids:
            messagebox.showinfo("알림", "표의 '선택' 칸을 클릭해 요약할 기사를 먼저 골라주세요.")
            return
        ids = list(self.selected_ids)
        hidden = len(self.selected_ids - {a.id for a in self.articles})
        if hidden and not messagebox.askyesno(
            "확인", f"지금 목록에 안 보이는 {hidden}건을 포함해 모두 {len(ids)}건을 요약합니다. 계속할까요?"
        ):
            return
        self._set_summarizing(True)
        self.status_var.set("요약 준비 중...")
        threading.Thread(target=self._summarize_worker, args=(ids, mode), daemon=True).start()

    def _summarize_worker(self, ids, mode):
        try:
            fmt = load_format(str(FORMAT_CONFIG))
            selected_articles = self.store.get_by_ids(ids)
            entries = []
            total = len(selected_articles)
            for i, a in enumerate(selected_articles, start=1):
                self.task_queue.put(("progress", (i, total, a.title)))
                full_text = fetch_full_text(a.url)
                body = summarize_article(a, full_text, mode=mode)
                entries.append(SummaryEntry(index=i, article=a, body=body))

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(OUTPUT_DIR / f"summary_{ts}.{output_extension(fmt)}")
            write_summaries(fmt, entries, output_path)
            self.store.set_summarized(ids, True)
            self.store.set_checked(ids, False)
            preview = render_txt(fmt, entries)
            self.task_queue.put(("summarize_done", (preview, output_path, len(entries))))
        except Exception as exc:
            self.task_queue.put(("summarize_error", str(exc)))

    def start_url_summarize(self, mode: str):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showinfo("알림", "요약할 웹페이지 주소(URL)를 입력하세요.")
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        self._set_summarizing(True)
        self.status_var.set(f"페이지 가져오는 중: {url}")
        threading.Thread(target=self._url_summarize_worker, args=(url, mode), daemon=True).start()

    def _url_summarize_worker(self, url, mode):
        """목록에 없는 개별 웹페이지 주소를 직접 받아 본문을 요약 (DB 에는 저장하지 않음)."""
        try:
            fmt = load_format(str(FORMAT_CONFIG))
            title, full_text = fetch_page(url)
            if not full_text:
                raise RuntimeError(f"페이지 본문을 가져오지 못했습니다.\n{url}")
            article = Article(source=urlparse(url).netloc, title=title or url, url=url)
            body = summarize_article(article, full_text, mode=mode)
            entries = [SummaryEntry(index=1, article=article, body=body)]

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(OUTPUT_DIR / f"summary_{ts}.{output_extension(fmt)}")
            write_summaries(fmt, entries, output_path)
            self.task_queue.put(("url_summarize_done", (render_txt(fmt, entries), output_path)))
        except Exception as exc:
            self.task_queue.put(("summarize_error", str(exc)))

    def copy_result(self):
        text = self.result_text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("알림", "복사할 결과가 없습니다.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set("결과를 클립보드에 복사했습니다 (메일·문서에 붙여넣기 가능)")

    def open_output_dir(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if hasattr(os, "startfile"):
                os.startfile(OUTPUT_DIR)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(OUTPUT_DIR)])
        except Exception as exc:
            messagebox.showerror("폴더 열기 실패", f"{OUTPUT_DIR}\n{exc}")

    def save_as(self):
        if not self.last_output_path or not Path(self.last_output_path).exists():
            messagebox.showinfo("알림", "먼저 요약을 생성하세요.")
            return
        ext = Path(self.last_output_path).suffix
        dest = filedialog.asksaveasfilename(
            defaultextension=ext,
            initialfile=Path(self.last_output_path).name,
            filetypes=[("파일", f"*{ext}")],
        )
        if dest:
            shutil.copyfile(self.last_output_path, dest)
            messagebox.showinfo("저장 완료", f"저장했습니다:\n{dest}")

    # ---------------- 사이트 관리 ----------------
    def open_source_manager(self):
        SourceManagerDialog(self)

    # ---------------- 백그라운드 작업 결과 처리 ----------------
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.task_queue.get_nowait()
                self._handle_task_result(kind, payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _handle_task_result(self, kind, payload):
        if kind == "crawl_progress":
            i, n, name = payload
            self.status_var.set(f"수집 중 ({i}/{n}): {name}")
        elif kind == "crawl_done":
            found, added, report = payload
            failed = sum(1 for r in report if r.error)
            self.crawl_btn.config(state="normal", text="🔄 기사 수집")
            self.new_only_check.config(state="normal")
            self.status_var.set(
                f"수집 완료: 조회 {found}건 · 신규 저장 {added}건"
                + (f" · 실패 {failed}곳 (아래 결과창 참고)" if failed else "")
            )
            self.last_output_path = None
            self.result_text.delete("1.0", "end")
            self.result_text.insert(
                "1.0",
                f"[사이트별 수집 결과] 신규 {added}건은 목록에 노란색으로 표시됩니다 (위 '신규만' 체크 시 신규만 보기).\n"
                f"{format_report(report)}\n",
            )
            self.refresh_list()
        elif kind == "crawl_error":
            self.crawl_btn.config(state="normal", text="🔄 기사 수집")
            self.status_var.set("수집 실패")
            messagebox.showerror("수집 실패", payload)
        elif kind == "progress":
            i, total, title = payload
            self.status_var.set(f"요약 중 ({i}/{total}): {title}")
        elif kind == "summarize_done":
            preview, output_path, n = payload
            self._set_summarizing(False)
            self.status_var.set(f"요약 완료: {n}건 → {output_path}")
            self.last_output_path = output_path
            self.result_text.delete("1.0", "end")
            self.result_text.insert("1.0", preview)
            self.refresh_list()
            if self.unsummarized_only_var.get():
                # '요약 안 한 것만' 화면에서는 방금 요약한 기사가 목록에서 빠지므로 선택도 함께 푼다
                self.selected_ids &= {a.id for a in self.articles}
                self._update_selection_label()
        elif kind == "url_summarize_done":
            preview, output_path = payload
            self._set_summarizing(False)
            self.status_var.set(f"페이지 요약 완료 → {output_path}")
            self.last_output_path = output_path
            self.result_text.delete("1.0", "end")
            self.result_text.insert("1.0", preview)
        elif kind == "summarize_error":
            self._set_summarizing(False)
            self.status_var.set("요약 실패")
            messagebox.showerror("요약 실패", payload)


class SourceManagerDialog(Toplevel):
    """수집 대상 사이트 목록 확인, 사용/중지, 삭제, RSS 사이트 추가 창."""

    def __init__(self, parent: App):
        super().__init__(parent)
        self.title("수집 대상 사이트 관리")
        self.geometry("760x480")
        self.transient(parent)

        list_frame = ttk.Frame(self, padding=8)
        list_frame.pack(fill="both", expand=True)
        cols = ("enabled", "tag", "name", "type", "url")
        self.listbox = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="browse")
        for col, text, width in (
            ("enabled", "사용", 45), ("tag", "태그", 90), ("name", "이름", 210), ("type", "종류", 50), ("url", "URL", 330),
        ):
            self.listbox.heading(col, text=text)
            self.listbox.column(col, width=width, anchor="center" if col in ("enabled", "type") else "w")
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-1>", lambda e: self.toggle_enabled())
        self._load_sources()

        row = ttk.Frame(self, padding=(8, 0))
        row.pack(fill="x")
        ttk.Label(row, text="사이트를 고른 뒤 →", foreground="#555555").pack(side="left")
        ttk.Button(row, text="사용/중지 전환", command=self.toggle_enabled).pack(side="left", padx=4)
        ttk.Button(row, text="삭제", command=self.delete_source).pack(side="left")

        form = ttk.LabelFrame(self, text="RSS 사이트 추가", padding=8)
        form.pack(fill="x", padx=8, pady=8)
        form.columnconfigure(1, weight=1)

        self.name_var = StringVar()
        self.url_var = StringVar()
        self.tag_var = StringVar()

        ttk.Label(form, text="이름").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, padx=4, sticky="we")
        ttk.Label(form, text="태그").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.tag_var, width=12).grid(row=0, column=3, padx=4)

        ttk.Label(form, text="RSS URL").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(form, textvariable=self.url_var).grid(
            row=1, column=1, columnspan=3, padx=4, pady=(4, 0), sticky="we"
        )

        ttk.Button(form, text="추가", command=self.add_source).grid(row=2, column=3, sticky="e", pady=(8, 0))

    def _load_sources(self):
        self.listbox.delete(*self.listbox.get_children())
        self.sources = load_sources(str(SOURCES_CONFIG))
        for i, s in enumerate(self.sources):
            on = "✔" if s.get("enabled", True) else "–"
            self.listbox.insert(
                "", "end", iid=str(i), values=(on, s.get("tag", ""), s["name"], s.get("type", "rss"), s["url"])
            )

    def _selected_index(self):
        sel = self.listbox.selection()
        if not sel:
            messagebox.showinfo("알림", "목록에서 사이트를 먼저 고르세요.", parent=self)
            return None
        return int(sel[0])

    def toggle_enabled(self):
        i = self._selected_index()
        if i is None:
            return
        src = self.sources[i]
        src["enabled"] = not src.get("enabled", True)
        save_sources(str(SOURCES_CONFIG), self.sources)
        self._load_sources()
        self.listbox.selection_set(str(i))

    def delete_source(self):
        i = self._selected_index()
        if i is None:
            return
        name = self.sources[i]["name"]
        if not messagebox.askyesno(
            "삭제 확인", f"'{name}' 사이트를 목록에서 삭제할까요?\n(이미 수집한 기사는 그대로 남습니다)", parent=self
        ):
            return
        del self.sources[i]
        save_sources(str(SOURCES_CONFIG), self.sources)
        self._load_sources()

    def add_source(self):
        name = self.name_var.get().strip()
        url = self.url_var.get().strip()
        tag = self.tag_var.get().strip() or name
        if not name or not url:
            messagebox.showwarning("입력 필요", "이름과 URL을 입력하세요.", parent=self)
            return
        if any(s.get("name") == name for s in self.sources):
            messagebox.showwarning("중복", f"'{name}' 이름의 사이트가 이미 있습니다.", parent=self)
            return
        self.sources.append(
            {"name": name, "type": "rss", "url": url, "tag": tag, "region": "Custom", "enabled": True}
        )
        save_sources(str(SOURCES_CONFIG), self.sources)
        self.name_var.set("")
        self.url_var.set("")
        self.tag_var.set("")
        self._load_sources()
        messagebox.showinfo("완료", f"'{name}' 사이트를 추가했습니다.", parent=self)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
