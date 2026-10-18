import json
import math
import shutil
import struct
import subprocess
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
import time as clock
import wave


WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
SCHEDULE_FILE = Path.home() / ".school_bell" / "weekly_schedules.json"


def current_weekday() -> str:
    return WEEKDAYS[(datetime.now().weekday() + 1) % len(WEEKDAYS)]


@dataclass(frozen=True)
class ScheduleItem:
    title: str
    kind: str
    start: int
    end: int


def build_schedule(
    lesson_count: int,
    lesson_minutes: int,
    short_break_minutes: int,
    recess_after: int,
    recess_minutes: int,
    skip_break_after: int,
    lesson_names: list[str] | None = None,
) -> list[ScheduleItem]:
    """Build lesson and break periods as offsets in seconds from the start."""
    items = []
    offset = 0

    for lesson in range(1, lesson_count + 1):
        end = offset + lesson_minutes * 60
        lesson_name = (
            lesson_names[lesson - 1].strip()
            if lesson_names and lesson - 1 < len(lesson_names)
            else ""
        )
        items.append(
            ScheduleItem(lesson_name or f"Lesson {lesson}", "lesson", offset, end)
        )
        offset = end

        if lesson == lesson_count:
            continue

        if lesson == recess_after:
            break_minutes = recess_minutes
            break_title = "Main recess"
        elif lesson == skip_break_after:
            break_minutes = 0
            break_title = ""
        else:
            break_minutes = short_break_minutes
            break_title = "Short break"

        if break_minutes:
            end = offset + break_minutes * 60
            items.append(ScheduleItem(break_title, "break", offset, end))
            offset = end

    return items


class SchoolBellApp:
    BACKGROUND = "#f3f6fb"
    NAVY = "#172554"
    BLUE = "#2563eb"
    TEXT = "#1e293b"
    MUTED = "#64748b"
    GREEN = "#15803d"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("School Bell")
        self.root.geometry("1020x1020")
        self.root.minsize(850, 850)
        self.root.configure(bg=self.BACKGROUND)

        self.profiles = self._load_profiles()
        today = current_weekday()
        self.day_name = tk.StringVar(
            value=today if today in self.profiles else "Sunday"
        )
        self.lesson_count = tk.StringVar(value="7")
        self.lesson_minutes = tk.StringVar(value="45")
        self.short_break_minutes = tk.StringVar(value="5")
        self.bell_duration_seconds = tk.StringVar(value="3")
        self.recess_after = tk.StringVar(value="3")
        self.recess_minutes = tk.StringVar(value="15")
        self.skip_break_after = tk.StringVar(value="Lesson 3")
        self.start_time = tk.StringVar(value="08:00")
        self.lesson_names = [f"Lesson {number}" for number in range(1, 13)]
        self.loading_profile = False
        self.day_configured = False

        self.schedule: list[ScheduleItem] = []
        self.rung_lessons: set[int] = set()
        self.elapsed_seconds = 0.0
        self.last_tick = 0.0
        self.bell_after_id = None
        self.bell_end_time = 0.0
        self.bell_process: subprocess.Popen | None = None
        self.bell_sound_path = SCHEDULE_FILE.parent / "bell.wav"
        self.running = False
        self.paused = False
        self.locked_widgets: list[tk.Widget] = []
        self.start_datetime = datetime.now()
        self.start_button: ttk.Button
        self.pause_button: ttk.Button
        self.countdown_label: tk.Label
        self.status_label: tk.Label
        self.next_label: tk.Label
        self.table: ttk.Treeview
        self.skip_combo: ttk.Combobox
        self.day_combo: ttk.Combobox

        self._configure_style()
        self._create_widgets()
        self._load_selected_day()
        self._refresh_preview()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _configure_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="white",
            fieldbackground="white",
            foreground=self.TEXT,
            rowheight=32,
            font=("TkDefaultFont", 10),
        )
        style.configure(
            "Treeview.Heading",
            background="#e8eef9",
            foreground=self.NAVY,
            font=("TkDefaultFont", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", "#dbeafe")])

    def _card(self, parent, **kwargs):
        return tk.Frame(
            parent,
            bg="white",
            highlightbackground="#e2e8f0",
            highlightthickness=1,
            **kwargs,
        )

    def _create_widgets(self):
        outer = tk.Frame(self.root, bg=self.BACKGROUND, padx=24, pady=18)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=self.NAVY, padx=24, pady=18)
        header.pack(fill="x", pady=(0, 14))
        tk.Label(
            header,
            text="School Bell",
            bg=self.NAVY,
            fg="white",
            font=("TkDefaultFont", 22, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            header,
            text="Organize lessons and breaks, and let the app handle the bell",
            bg=self.NAVY,
            fg="#dbeafe",
            font=("TkDefaultFont", 11),
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        timer_card = self._card(outer, padx=18, pady=12)
        timer_card.pack(fill="x", pady=(0, 14))
        self.status_label = tk.Label(
            timer_card,
            text="Ready to start the school day",
            bg="white",
            fg=self.BLUE,
            font=("TkDefaultFont", 12, "bold"),
            anchor="w",
        )
        self.status_label.pack(fill="x")
        self.countdown_label = tk.Label(
            timer_card,
            text="00:00",
            bg="white",
            fg=self.NAVY,
            font=("TkDefaultFont", 54, "bold"),
        )
        self.countdown_label.pack(pady=(0, 1))
        self.next_label = tk.Label(
            timer_card,
            text='Set up the schedule, then click "Start School Day"',
            bg="white",
            fg=self.MUTED,
            font=("TkDefaultFont", 11),
            anchor="w",
        )
        self.next_label.pack(fill="x", pady=(0, 8))

        controls = tk.Frame(timer_card, bg="white")
        controls.pack()
        self.start_button = ttk.Button(
            controls, text="▶  Start School Day", command=self.start
        )
        self.start_button.grid(row=0, column=0, padx=5)
        self.pause_button = ttk.Button(
            controls, text="Pause", command=self.toggle_pause, state="disabled"
        )
        self.pause_button.grid(row=0, column=1, padx=5)
        ttk.Button(controls, text="Reset", command=self.reset).grid(
            row=0, column=2, padx=5
        )
        ttk.Button(controls, text="Test Bell", command=self.ring).grid(
            row=0, column=3, padx=5
        )

        settings = self._card(outer, padx=18, pady=14)
        settings.pack(fill="x", pady=(0, 14))
        tk.Label(
            settings,
            text="School Day Settings",
            bg="white",
            fg=self.NAVY,
            font=("TkDefaultFont", 14, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        day_row = tk.Frame(settings, bg="white")
        day_row.pack(fill="x", pady=(0, 6))
        self._add_field(
            day_row,
            "Configure Weekday",
            self.day_name,
            0,
            0,
            values=WEEKDAYS,
        )
        self.day_combo = self.locked_widgets[-1]
        self.day_combo.bind("<<ComboboxSelected>>", self._change_day)

        grid = tk.Frame(settings, bg="white")
        grid.pack(fill="x")
        for column in range(4):
            grid.grid_columnconfigure(column, weight=1)

        self._add_field(grid, "Start Time (HH:MM)", self.start_time, 0, 0)
        self._add_field(grid, "Number of Lessons", self.lesson_count, 0, 1, limits=(1, 12))
        self._add_field(
            grid, "Lesson Duration (minutes)", self.lesson_minutes, 0, 2, limits=(1, 240)
        )
        self._add_field(
            grid,
            "Short Break (minutes)",
            self.short_break_minutes,
            0,
            3,
            limits=(0, 60),
        )
        self._add_field(grid, "Main Recess After Lesson", self.recess_after, 1, 0, limits=(1, 12))
        self._add_field(
            grid, "Main Recess Duration (minutes)", self.recess_minutes, 1, 1, limits=(1, 120)
        )
        self.skip_combo = self._add_field(
            grid,
            "Skip 5-Minute Break After",
            self.skip_break_after,
            1,
            2,
            values=["None"],
        )
        self._add_field(
            grid,
            "Bell Ring Duration (seconds)",
            self.bell_duration_seconds,
            1,
            3,
            limits=(1, 60),
        )

        tk.Label(
            settings,
            text="The main recess replaces the short break after the selected lesson. "
            "You can skip one short break per day.",
            bg="white",
            fg=self.MUTED,
            font=("TkDefaultFont", 9),
            anchor="w",
            wraplength=850,
            justify="left",
        ).pack(fill="x", pady=(9, 0))

        schedule_card = self._card(outer, padx=18, pady=14)
        schedule_card.pack(fill="both", expand=True)
        tk.Label(
            schedule_card,
            text="Today's Schedule",
            bg="white",
            fg=self.NAVY,
            font=("TkDefaultFont", 14, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 8))
        self.table = ttk.Treeview(
            schedule_card,
            columns=("duration", "end", "start", "event"),
            show="headings",
            height=8,
        )
        self.table.heading("event", text="Activity", anchor="w")
        self.table.heading("start", text="Start", anchor="center")
        self.table.heading("end", text="End", anchor="center")
        self.table.heading("duration", text="Duration", anchor="center")
        self.table.column("event", width=400, anchor="w")
        self.table.column("start", width=120, anchor="center")
        self.table.column("end", width=120, anchor="center")
        self.table.column("duration", width=120, anchor="center")
        self.table.pack(fill="both", expand=True)
        self.table.tag_configure("lesson", foreground=self.NAVY)
        self.table.tag_configure("break", foreground=self.GREEN)
        self.table.bind("<Double-1>", self._rename_lesson)
        tk.Label(
            schedule_card,
            text="Double-click a lesson in the schedule to edit its name.",
            bg="white",
            fg=self.MUTED,
            font=("TkDefaultFont", 9),
            anchor="w",
        ).pack(fill="x", pady=(7, 0))

        self._settings_vars = [
            self.lesson_count,
            self.lesson_minutes,
            self.short_break_minutes,
            self.bell_duration_seconds,
            self.recess_after,
            self.recess_minutes,
            self.skip_break_after,
            self.start_time,
        ]
        for variable in self._settings_vars:
            variable.trace_add("write", self._settings_changed)

    @staticmethod
    def _default_profile():
        return {
            "start_time": "08:00",
            "lesson_count": 7,
            "lesson_minutes": 45,
            "short_break_minutes": 5,
            "bell_duration_seconds": 3,
            "recess_after": 3,
            "recess_minutes": 15,
            "skip_break_after": 3,
            "lesson_names": [f"Lesson {number}" for number in range(1, 13)],
        }

    def _load_profiles(self):
        if not SCHEDULE_FILE.exists():
            return {"Sunday": self._default_profile()}
        try:
            with SCHEDULE_FILE.open(encoding="utf-8") as schedule_file:
                data = json.load(schedule_file)
            if not isinstance(data, dict):
                raise ValueError("The weekly schedule must contain a JSON object.")
            profiles = {}
            for day, profile in data.items():
                if day not in WEEKDAYS or not isinstance(profile, dict):
                    raise ValueError(f"Invalid schedule entry for {day!r}.")
                normalized = self._default_profile()
                normalized.update(profile)
                normalized["lesson_count"] = int(normalized["lesson_count"])
                normalized["lesson_minutes"] = int(normalized["lesson_minutes"])
                normalized["short_break_minutes"] = int(
                    normalized["short_break_minutes"]
                )
                normalized["bell_duration_seconds"] = int(
                    normalized["bell_duration_seconds"]
                )
                normalized["recess_after"] = int(normalized["recess_after"])
                normalized["recess_minutes"] = int(normalized["recess_minutes"])
                normalized["skip_break_after"] = int(
                    normalized["skip_break_after"]
                )
                if not isinstance(normalized["start_time"], str):
                    raise ValueError(f"Invalid start time for {day}.")
                datetime.strptime(normalized["start_time"], "%H:%M")
                count = normalized["lesson_count"]
                if not 1 <= count <= 12:
                    raise ValueError(f"Invalid lesson count for {day}.")
                if not 1 <= normalized["lesson_minutes"] <= 240:
                    raise ValueError(f"Invalid lesson duration for {day}.")
                if not 0 <= normalized["short_break_minutes"] <= 60:
                    raise ValueError(f"Invalid short break duration for {day}.")
                if not 1 <= normalized["bell_duration_seconds"] <= 60:
                    raise ValueError(f"Invalid bell duration for {day}.")
                if not 1 <= normalized["recess_minutes"] <= 120:
                    raise ValueError(f"Invalid recess duration for {day}.")
                if count > 1 and not 1 <= normalized["recess_after"] < count:
                    raise ValueError(f"Invalid main recess position for {day}.")
                if not 0 <= normalized["skip_break_after"] < count:
                    raise ValueError(f"Invalid skipped break position for {day}.")
                names = normalized.get("lesson_names")
                if not isinstance(names, list):
                    raise ValueError(f"Lesson names for {day} must be a list.")
                normalized["lesson_names"] = [
                    name if isinstance(name, str) else f"Lesson {number}"
                    for number, name in enumerate(names[:12], start=1)
                ]
                normalized["lesson_names"] += [
                    f"Lesson {number}"
                    for number in range(len(normalized["lesson_names"]) + 1, 13)
                ]
                profiles[day] = normalized
            profiles.setdefault("Sunday", self._default_profile())
            return profiles
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            messagebox.showerror(
                "Unable to Load Weekly Schedule",
                f"The saved schedule could not be loaded:\n{error}\n\n"
                "A default Sunday schedule will be used.",
                parent=self.root,
            )
            return {"Sunday": self._default_profile()}

    def _load_selected_day(self):
        self.loading_profile = True
        profile = self.profiles.get(self.day_name.get())
        self.day_configured = profile is not None
        values = profile or self._default_profile()
        for variable, key in (
            (self.start_time, "start_time"),
            (self.lesson_count, "lesson_count"),
            (self.lesson_minutes, "lesson_minutes"),
            (self.short_break_minutes, "short_break_minutes"),
            (self.bell_duration_seconds, "bell_duration_seconds"),
            (self.recess_after, "recess_after"),
            (self.recess_minutes, "recess_minutes"),
        ):
            variable.set(str(values[key]))
        skip_after = int(values["skip_break_after"])
        self.skip_break_after.set("None" if skip_after == 0 else f"Lesson {skip_after}")
        self.lesson_names = list(values["lesson_names"])
        self.loaded_day = self.day_name.get()
        self._update_skip_options()
        self.loading_profile = False

    def _change_day(self, _event=None):
        if self.running or self.paused:
            return
        self._save_current_day(self.loaded_day)
        self._load_selected_day()
        self._refresh_preview()

    def _save_current_day(self, day=None):
        if self.loading_profile:
            return
        try:
            (
                count,
                lesson_minutes,
                short_break,
                bell_duration,
                recess_after,
                recess_minutes,
                skip_after,
                start_clock,
            ) = self._read_settings()
        except (ValueError, TypeError):
            return
        day = day or self.day_name.get()
        self.profiles[day] = {
            "start_time": start_clock.strftime("%H:%M"),
            "lesson_count": count,
            "lesson_minutes": lesson_minutes,
            "short_break_minutes": short_break,
            "bell_duration_seconds": bell_duration,
            "recess_after": recess_after,
            "recess_minutes": recess_minutes,
            "skip_break_after": skip_after,
            "lesson_names": self.lesson_names,
        }
        self.day_configured = True
        try:
            SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary_file = SCHEDULE_FILE.with_suffix(".tmp")
            with temporary_file.open("w", encoding="utf-8") as schedule_file:
                json.dump(self.profiles, schedule_file, ensure_ascii=False, indent=2)
            temporary_file.replace(SCHEDULE_FILE)
        except OSError as error:
            messagebox.showerror(
                "Unable to Save Schedule",
                f"The schedule for {day} could not be saved:\n{error}",
                parent=self.root,
            )

    def _add_field(
        self, parent, label, variable, row, column, values=None, limits=(1, 12)
    ):
        cell = tk.Frame(parent, bg="white", padx=6, pady=4)
        cell.grid(row=row, column=column, sticky="ew")
        tk.Label(
            cell,
            text=label,
            bg="white",
            fg=self.TEXT,
            font=("TkDefaultFont", 9, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 4))
        if values is not None:
            widget = ttk.Combobox(
                cell, textvariable=variable, values=values, state="readonly", justify="right"
            )
        elif variable is self.start_time:
            widget = ttk.Entry(cell, textvariable=variable, justify="right")
        else:
            widget = ttk.Spinbox(
                cell,
                textvariable=variable,
                from_=limits[0],
                to=limits[1],
                justify="right",
            )
        widget.pack(fill="x")
        widget.bind("<KeyRelease>", self._settings_changed)
        widget.bind("<<ComboboxSelected>>", self._settings_changed)
        self.locked_widgets.append(widget)
        return widget

    def _settings_changed(self, *_):
        if not self.loading_profile and not self.running and not self.paused:
            was_configured = self.day_configured
            if self._refresh_preview():
                self._save_current_day()
                if not was_configured:
                    self._refresh_preview()

    def _read_settings(self):
        count = int(self.lesson_count.get())
        lesson_minutes = int(self.lesson_minutes.get())
        short_break = int(self.short_break_minutes.get())
        bell_duration = int(self.bell_duration_seconds.get())
        recess_after = int(self.recess_after.get())
        recess_minutes = int(self.recess_minutes.get())

        if not 1 <= count <= 12:
            raise ValueError("The number of lessons must be between 1 and 12.")
        if not 1 <= lesson_minutes <= 240:
            raise ValueError("Lesson duration must be between 1 and 240 minutes.")
        if not 0 <= short_break <= 60:
            raise ValueError("Short break duration must be between 0 and 60 minutes.")
        if not 1 <= bell_duration <= 60:
            raise ValueError("Bell ring duration must be between 1 and 60 seconds.")
        if not 1 <= recess_after < count and count > 1:
            raise ValueError("Main recess must be scheduled after a lesson before the last.")
        if count == 1:
            recess_after = 1
        if not 1 <= recess_minutes <= 120:
            raise ValueError("Main recess duration must be between 1 and 120 minutes.")

        skip_value = self.skip_break_after.get()
        if skip_value == "None":
            skip_after = 0
        else:
            skip_after = int(skip_value.split()[-1])
            if not 1 <= skip_after < count:
                raise ValueError("Choose a lesson that is followed by a break.")

        try:
            start_clock = datetime.strptime(self.start_time.get().strip(), "%H:%M").time()
        except ValueError as error:
            raise ValueError("Enter the start time in HH:MM format, for example 08:00.") from error

        return (
            count,
            lesson_minutes,
            short_break,
            bell_duration,
            recess_after,
            recess_minutes,
            skip_after,
            start_clock,
        )

    def _refresh_preview(self):
        try:
            (
                count,
                lesson_minutes,
                short_break,
                _bell_duration,
                recess_after,
                recess_minutes,
                skip_after,
                start_clock,
            ) = self._read_settings()
        except (ValueError, TypeError):
            self._update_skip_options()
            return False

        self.schedule = build_schedule(
            count,
            lesson_minutes,
            short_break,
            recess_after,
            recess_minutes,
            skip_after,
            self.lesson_names,
        )
        self.start_datetime = datetime.combine(datetime.now().date(), start_clock)
        self._update_skip_options(count)
        self._populate_table()
        if self.day_configured:
            self.start_button.configure(state="normal")
            self.status_label.configure(
                text=f"Weekly schedule preview: {self.day_name.get()}", fg=self.BLUE
            )
            self.next_label.configure(
                text="Double-click a lesson in the schedule to change its name."
            )
        else:
            self.start_button.configure(state="disabled")
            self.status_label.configure(
                text=f"No schedule configured for {self.day_name.get()}", fg=self.MUTED
            )
            self.next_label.configure(
                text="Edit this day's settings to create and save its schedule."
            )
        return True

    def _update_skip_options(self, count=None):
        try:
            count = count or int(self.lesson_count.get())
        except ValueError:
            count = 5
        choices = ["None"] + [f"Lesson {number}" for number in range(1, count)]
        self.skip_combo.configure(values=choices)
        if self.skip_break_after.get() not in choices:
            self.skip_break_after.set("None")

    def _populate_table(self):
        for row in self.table.get_children():
            self.table.delete(row)
        for index, item in enumerate(self.schedule):
            start = self.start_datetime.timestamp() + item.start
            end = self.start_datetime.timestamp() + item.end
            start_text = datetime.fromtimestamp(start).strftime("%H:%M")
            end_text = datetime.fromtimestamp(end).strftime("%H:%M")
            duration = (item.end - item.start) // 60
            self.table.insert(
                "",
                "end",
                iid=f"item-{index}",
                values=(f"{duration} min", end_text, start_text, item.title),
                tags=(item.kind,),
            )

    def _rename_lesson(self, event):
        if self.running or self.paused or self.table.identify_column(event.x) != "#4":
            return
        row_id = self.table.identify_row(event.y)
        if not row_id:
            return
        item_index = int(row_id.removeprefix("item-"))
        item = self.schedule[item_index]
        if item.kind != "lesson":
            return
        lesson_number = sum(
            schedule_item.kind == "lesson"
            for schedule_item in self.schedule[: item_index + 1]
        )
        name = simpledialog.askstring(
            "Set Lesson Name",
            f"Enter a name for lesson {lesson_number}:",
            initialvalue=item.title,
            parent=self.root,
        )
        if name is None:
            return
        self.lesson_names[lesson_number - 1] = name.strip() or f"Lesson {lesson_number}"
        self._refresh_preview()
        self._save_current_day()
        self._refresh_preview()

    def start(self):
        today = current_weekday()
        if not self.day_configured:
            messagebox.showerror(
                "No Schedule",
                f"No schedule has been configured for {self.day_name.get()}.",
                parent=self.root,
            )
            return
        if self.day_name.get() != today:
            messagebox.showerror(
                "Wrong Weekday",
                f"Today is {today}. Select today's schedule before starting the bell.",
                parent=self.root,
            )
            return
        try:
            (
                count,
                lesson_minutes,
                short_break,
                _bell_duration,
                recess_after,
                recess_minutes,
                skip_after,
                start_clock,
            ) = self._read_settings()
        except (ValueError, TypeError) as error:
            messagebox.showerror("Invalid Settings", str(error), parent=self.root)
            return

        now = datetime.now()
        planned_start = datetime.combine(now.date(), start_clock)
        self.start_datetime = planned_start
        self.schedule = build_schedule(
            count,
            lesson_minutes,
            short_break,
            recess_after,
            recess_minutes,
            skip_after,
            self.lesson_names,
        )
        elapsed_at_start = (now - planned_start).total_seconds()
        if elapsed_at_start >= self.schedule[-1].end:
            messagebox.showerror(
                "School Day Finished",
                "The scheduled school day has already ended.",
                parent=self.root,
            )
            return
        self._populate_table()
        self.rung_lessons = {
            index
            for index, item in enumerate(self.schedule)
            if item.kind == "lesson" and item.end <= elapsed_at_start
        }
        self.elapsed_seconds = elapsed_at_start
        self.last_tick = clock.monotonic()
        self.running = True
        self.paused = False
        self._set_settings_state("disabled")
        self.start_button.configure(state="disabled")
        self.pause_button.configure(text="Pause", state="normal")
        self._tick()

    def toggle_pause(self):
        if not self.running and not self.paused:
            return
        if self.running:
            self.elapsed_seconds += clock.monotonic() - self.last_tick
            self.running = False
            self.paused = True
            self.pause_button.configure(text="Resume")
            self.status_label.configure(text="Paused", fg="#b45309")
            return

        self.last_tick = clock.monotonic()
        self.running = True
        self.paused = False
        self.pause_button.configure(text="Pause")
        self._tick()

    def reset(self):
        self.running = False
        self.paused = False
        self._stop_bell()
        self.elapsed_seconds = 0.0
        self.rung_lessons.clear()
        self.root.after_cancel(self._tick_id) if hasattr(self, "_tick_id") else None
        self._set_settings_state("normal")
        self.start_button.configure(state="normal")
        self.pause_button.configure(text="Pause", state="disabled")
        self.status_label.configure(text="Ready to start the school day", fg=self.BLUE)
        self.countdown_label.configure(text="00:00")
        self.next_label.configure(text='Set up the schedule, then click "Start School Day"')
        self._refresh_preview()

    def _tick(self):
        if not self.running:
            return
        current = self.elapsed_seconds + clock.monotonic() - self.last_tick

        for index, item in enumerate(self.schedule):
            if item.kind == "lesson" and item.end <= current and index not in self.rung_lessons:
                self.rung_lessons.add(index)
                self.ring()

        if current < 0:
            self.status_label.configure(text="School day will start at the scheduled time", fg=self.BLUE)
            self.countdown_label.configure(text=self._format_time(-current))
            self.next_label.configure(
                text=f"First lesson starts at {self.start_datetime.strftime('%H:%M')}"
            )
        else:
            active = next(
                (item for item in self.schedule if item.start <= current < item.end), None
            )
            if active is None:
                self.running = False
                self.paused = False
                self.status_label.configure(text="School day complete", fg=self.GREEN)
                self.countdown_label.configure(text="00:00")
                self.next_label.configure(text="Great job! All lessons are finished.")
                self.pause_button.configure(state="disabled")
                self._set_settings_state("normal")
                self.start_button.configure(state="normal")
                return

            remaining = active.end - current
            if active.kind == "lesson":
                self.status_label.configure(text=f"Now in session: {active.title}", fg=self.BLUE)
                upcoming = next(
                    (item for item in self.schedule if item.start >= active.end), None
                )
                next_text = (
                    f"Next: {upcoming.title}" if upcoming else "This is the last lesson of the day"
                )
            else:
                self.status_label.configure(text=f"{active.title} in progress", fg=self.GREEN)
                next_lesson = next(
                    (item for item in self.schedule if item.start >= active.end), None
                )
                next_text = (
                    f"Next: {next_lesson.title}" if next_lesson else "School day complete"
                )
            self.countdown_label.configure(text=self._format_time(remaining))
            self.next_label.configure(text=next_text)

        self._tick_id = self.root.after(200, self._tick)

    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds + 0.999))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    def ring(self):
        try:
            duration = int(self.bell_duration_seconds.get())
        except ValueError as error:
            messagebox.showerror(
                "Invalid Settings",
                "Bell ring duration must be a whole number of seconds.",
                parent=self.root,
            )
            return
        if not 1 <= duration <= 60:
            messagebox.showerror(
                "Invalid Settings",
                "Bell ring duration must be between 1 and 60 seconds.",
                parent=self.root,
            )
            return
        self._stop_bell()
        self.bell_end_time = clock.monotonic() + duration
        self._ring_bell_once()

    def _ring_bell_once(self):
        if clock.monotonic() >= self.bell_end_time:
            self.bell_after_id = None
            return
        try:
            self._play_bell_sound()
        except (OSError, RuntimeError) as error:
            messagebox.showerror("Unable to Play Bell", str(error), parent=self.root)
            self._stop_bell()
            return

        remaining = self.bell_end_time - clock.monotonic()
        if remaining > 0:
            self.bell_after_id = self.root.after(
                min(700, max(1, int(remaining * 1000))), self._ring_bell_once
            )
        else:
            self.bell_after_id = None

    def _create_bell_sound(self):
        self.bell_sound_path.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 44100
        duration = 0.55
        with wave.open(str(self.bell_sound_path), "wb") as sound_file:
            sound_file.setnchannels(1)
            sound_file.setsampwidth(2)
            sound_file.setframerate(sample_rate)
            samples = bytearray()
            for index in range(int(sample_rate * duration)):
                moment = index / sample_rate
                envelope = math.exp(-5.5 * moment)
                tone = (
                    math.sin(2 * math.pi * 880 * moment)
                    + 0.55 * math.sin(2 * math.pi * 1174.66 * moment)
                    + 0.2 * math.sin(2 * math.pi * 1760 * moment)
                )
                sample = int(14000 * envelope * tone / 1.75)
                samples.extend(struct.pack("<h", sample))
            sound_file.writeframes(samples)

    def _play_bell_sound(self):
        if self.bell_process is not None:
            if self.bell_process.poll() is None:
                return
            _, error_output = self.bell_process.communicate()
            return_code = self.bell_process.returncode
            self.bell_process = None
            if return_code:
                details = error_output.decode(errors="replace").strip()
                raise RuntimeError(details or "The audio player could not play the bell.")

        if not self.bell_sound_path.exists():
            self._create_bell_sound()

        if self.root.tk.call("tk", "windowingsystem") == "win32":
            import winsound

            winsound.PlaySound(
                str(self.bell_sound_path),
                winsound.SND_FILENAME | winsound.SND_ASYNC,
            )
            return

        if shutil.which("afplay"):
            command = ["afplay", str(self.bell_sound_path)]
        elif shutil.which("paplay"):
            command = ["paplay", str(self.bell_sound_path)]
        elif shutil.which("aplay"):
            command = ["aplay", "-q", str(self.bell_sound_path)]
        elif shutil.which("ffplay"):
            command = [
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "error",
                str(self.bell_sound_path),
            ]
        else:
            raise RuntimeError(
                "No supported audio player was found. Install paplay, aplay, or ffplay "
                "and check that your system audio is enabled."
            )
        self.bell_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def _stop_bell(self):
        if self.bell_after_id is not None:
            self.root.after_cancel(self.bell_after_id)
            self.bell_after_id = None
        if self.bell_process is not None:
            if self.bell_process.poll() is None:
                self.bell_process.terminate()
                try:
                    self.bell_process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self.bell_process.kill()
                    self.bell_process.wait()
            self.bell_process.communicate()
            self.bell_process = None
        if self.root.tk.call("tk", "windowingsystem") == "win32":
            import winsound

            winsound.PlaySound(None, 0)
        self.bell_end_time = 0.0

    def _set_settings_state(self, state):
        for widget in self.locked_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                continue
        if state == "disabled":
            self.skip_combo.configure(state="disabled")
            self.day_combo.configure(state="disabled")
        else:
            self.skip_combo.configure(state="readonly")
            self.day_combo.configure(state="readonly")

    def _close(self):
        self.running = False
        self._stop_bell()
        self.root.destroy()


def main():
    root = tk.Tk()
    SchoolBellApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
