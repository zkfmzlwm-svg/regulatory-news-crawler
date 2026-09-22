import queue
import shutil
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, Toplevel, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from src.crawler import crawl_all, load_sources, save_sources
from src.fetcher import fetch_full_text
from src.storage import DEFAULT_DB_PATH, Storage
from src.summarizer import (
    SummaryEntry,
    load_format,
    output_extension,
    render_txt,
    summarize_article,
    write_summaries,
)
from src.utils import clean_text

BASE_DIR = Path(__file__).resolve().parent
SOURCES_CONFIG = BASE_DIR / "config" / "sources.yaml"
FORMAT_CONFIG = BASE_DIR / "config" / "summary_format.yaml"
OUTPUT_DIR = BASE_DIR / "output"


class App(Tk):
    def __init__(self):
        super().__init__()
        self.title("해외 의약품 규제 뉴스 크롤러")
        self.geometry("1120x760")
        self.minsize(900, 600)

        self.store = Storage(DEFAULT_DB_PATH)
        self.articles = []
        self.selected_ids = set()
        self.last_output_path = None
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
        keyword_entry = ttk.Entry(toolbar, textvariable=self.keyword_var, width=20)
        keyword_entry.pack(side="left", padx=(4, 8))
        keyword_entry.bind("<Return>", lambda e: self.refresh_list())

        self.unchecked_only_var = BooleanVar()
        ttk.Checkbutton(
            toolbar,
            text="요약 안 한 것만",
            variable=self.unchecked_only_var,
            command=self.refresh_list,
        ).pack(side="left", padx=(0, 8))

        ttk.Button(toolbar, text="검색", command=self.refresh_list).pack(side="left")
        ttk.Button(toolbar, text="사이트 관리", command=self.open_source_manager).pack(side="right")

        hint = ttk.Label(
            self,
            text="※ 표의 '선택' 칸을 클릭해 요약할 기사를 고르고, 제목을 더블클릭하면 원문이 브라우저로 열립니다.",
            padding=(8, 0),
            foreground="#555555",
        )
        hint.pack(fill="x")

        list_frame = ttk.Frame(self, padding=8)
        list_frame.pack(fill="both", expand=True)

        columns = ("select", "date", "source", "title", "summarized")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="none")
        headers = {"select": "선택", "date": "날짜", "source": "출처", "title": "제목", "summarized": "요약됨"}
        widths = {"select": 50, "date": 90, "source": 130, "title": 600, "summarized": 60}
        anchors = {"select": "center", "date": "center", "source": "w", "title": "w", "summarized": "center"}
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], anchor=anchors[col])

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        action_frame = ttk.Frame(self, padding=8)
        action_frame.pack(fill="x")

        self.selection_label = ttk.Label(action_frame, text="0건 표시 · 0건 선택됨")
        self.selection_label.pack(side="left")

        ttk.Button(
            action_frame, text="🌍 번역 요약 (무료)", command=lambda: self.start_summarize("free")
        ).pack(side="right", padx=4)
        ttk.Button(
            action_frame, text="🆓 단순 요약 (영어)", command=lambda: self.start_summarize("simple")
        ).pack(side="right", padx=4)

        result_frame = ttk.LabelFrame(self, text="요약 결과", padding=8)
        result_frame.pack(fill="both", expand=False, padx=8, pady=(0, 8))
        self.result_text = ScrolledText(result_frame, height=14, wrap="word", font=("맑은 고딕", 10))
        self.result_text.pack(fill="both", expand=True)

        save_row = ttk.Frame(result_frame)
        save_row.pack(fill="x", pady=(6, 0))
        ttk.Button(save_row, text="📁 다른 이름으로 저장", command=self.save_as).pack(side="left")

        self.status_var = StringVar(value="준비됨")
        ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(8, 4)).pack(fill="x")

    # ---------------- 기사 목록 ----------------
    def refresh_list(self):
        keyword = self.keyword_var.get().strip() or None
        self.articles = self.store.list_articles(
            keyword=keyword, unchecked_only=self.unchecked_only_var.get(), limit=300
        )
        self.selected_ids &= {a.id for a in self.articles}
        self._render_tree()

    def _render_tree(self):
        self.tree.delete(*self.tree.get_children())
        for a in self.articles:
            mark = "☑" if a.id in self.selected_ids else "☐"
            done = "✅" if a.summarized else ""
            self.tree.insert(
                "",
                "end",
                iid=str(a.id),
                values=(mark, (a.published_at or "")[:10], a.tag or a.source, clean_text(a.title), done),
            )
        self.selection_label.config(text=f"{len(self.articles)}건 표시 · {len(self.selected_ids)}건 선택됨")

    def _on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        aid = int(row_id)
        if aid in self.selected_ids:
            self.selected_ids.discard(aid)
        else:
            self.selected_ids.add(aid)
        self._render_tree()

    def _on_tree_double_click(self, event):
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        article = next((a for a in self.articles if a.id == int(row_id)), None)
        if article:
            webbrowser.open(article.url)

    # ---------------- 기사 수집 ----------------
    def start_crawl(self):
        self.crawl_btn.config(state="disabled", text="수집 중...")
        self.status_var.set("등록된 사이트에서 기사를 가져오는 중...")
        threading.Thread(target=self._crawl_worker, daemon=True).start()

    def _crawl_worker(self):
        try:
            sources = load_sources(str(SOURCES_CONFIG))
            found = crawl_all(sources)
            added = self.store.add_articles(found)
            self.task_queue.put(("crawl_done", (len(found), added)))
        except Exception as exc:
            self.task_queue.put(("crawl_error", str(exc)))

    # ---------------- 요약 ----------------
    def start_summarize(self, mode: str):
        if not self.selected_ids:
            messagebox.showinfo("알림", "표의 '선택' 칸을 클릭해 요약할 기사를 먼저 골라주세요.")
            return
        ids = list(self.selected_ids)
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
        if kind == "crawl_done":
            found, added = payload
            self.crawl_btn.config(state="normal", text="🔄 기사 수집")
            self.status_var.set(f"수집 완료: 조회 {found}건 · 신규 저장 {added}건")
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
            self.status_var.set(f"요약 완료: {n}건 → {output_path}")
            self.last_output_path = output_path
            self.result_text.delete("1.0", "end")
            self.result_text.insert("1.0", preview)
            self.selected_ids.clear()
            self.refresh_list()
        elif kind == "summarize_error":
            self.status_var.set("요약 실패")
            messagebox.showerror("요약 실패", payload)


class SourceManagerDialog(Toplevel):
    """수집 대상 RSS 사이트 목록 확인 및 추가 창."""

    def __init__(self, parent: App):
        super().__init__(parent)
        self.title("수집 대상 사이트 관리")
        self.geometry("680x440")
        self.transient(parent)

        list_frame = ttk.Frame(self, padding=8)
        list_frame.pack(fill="both", expand=True)
        self.listbox = ttk.Treeview(list_frame, columns=("tag", "name", "url"), show="headings")
        self.listbox.heading("tag", text="태그")
        self.listbox.heading("name", text="이름")
        self.listbox.heading("url", text="URL")
        self.listbox.column("tag", width=90)
        self.listbox.column("name", width=200)
        self.listbox.column("url", width=340)
        self.listbox.pack(fill="both", expand=True)
        self._load_sources()

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
        for s in self.sources:
            self.listbox.insert("", "end", values=(s.get("tag", ""), s["name"], s["url"]))

    def add_source(self):
        name = self.name_var.get().strip()
        url = self.url_var.get().strip()
        tag = self.tag_var.get().strip() or name
        if not name or not url:
            messagebox.showwarning("입력 필요", "이름과 URL을 입력하세요.", parent=self)
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
