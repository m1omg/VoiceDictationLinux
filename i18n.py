"""The interface in English or Slovak: the tray menu, both settings windows, the status pop-up,
notifications and the installers' questions.

English is the source: t("Sounds") is "Zvuky" while the interface is in Slovak, else the text
itself, and also for a text missing from SK (tests/unit_i18n.py checks that none is missing).
Values go in through placeholders, never f-strings: t("Hold {key} to dictate", key=label).
"""
from __future__ import annotations

import locale
import os
import sys

LANGUAGES = {"en": "English", "sk": "Slovenčina"}
language = "en"


def set_language(code: str) -> None:
    global language
    language = code if code in LANGUAGES else "en"


def t(text: str, /, **values) -> str:  # (positional: a placeholder may be called {text} too)
    if language == "sk":
        text = SK.get(text, text)
    return text.format(**values) if values else text


def system_language() -> str:
    """"sk" when the system's own language is Slovak (a suggestion for the installers' first question)."""
    names = [os.environ.get(v, "") for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")]
    if sys.platform == "win32":
        try:
            import ctypes
            names.append(locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), ""))
        except (AttributeError, OSError):
            pass
    elif sys.platform == "darwin":
        try:
            import subprocess
            names.append(subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                                        text=True, timeout=5).stdout.split('"')[1])
        except (OSError, IndexError, subprocess.SubprocessError):
            pass
    first = next((n for n in names if n and n not in ("C", "POSIX", "C.UTF-8")), "")
    return "sk" if first.lower().startswith("sk") else "en"


SK: dict[str, str] = {
    # --- the tray menu ---
    "Hold {key} to dictate": "Na diktovanie podržte {key}",
    "Hold {key} to dictate, or tap it to start and stop": "Na diktovanie podržte {key} alebo ním ťuknite na začiatok a koniec",
    "Auto-detect (English / Slovak)": "Rozpoznať automaticky (angličtina / slovenčina)",
    "Type while speaking": "Písať počas hovorenia",
    "Instantly, correcting itself as it goes": "Okamžite, s priebežnými opravami",
    "Tap to start and stop": "Ťuknutím začať a skončiť",
    "Sounds": "Zvuky",
    "Main model: {model}": "Hlavný model: {model}",
    "downloading {progress}": "sťahuje sa {progress}",
    "download {size}": "stiahnuť {size}",
    "Run on: graphics card": "Beží na: grafickej karte",
    "Run on: processor": "Beží na: procesore",
    "Graphics card (GPU)": "Grafická karta (GPU)",
    "Graphics card (none usable)": "Grafická karta (žiadna použiteľná)",
    "Processor (CPU)": "Procesor (CPU)",
    "Status pop-up while dictating": "Stavové okienko počas diktovania",
    "Automatic: small, when the model runs on the processor": "Automaticky: malé, keď model beží na procesore",
    "Small": "Malé",
    "Large, for low vision": "Veľké, pre slabozrakých",
    "Off": "Vypnuté",
    "Large text": "Veľké písmo",
    "Clicking the icon opens the big settings window": "Kliknutie na ikonu otvorí veľké okno nastavení",
    "Text size and colours…": "Veľkosť písma a farby…",
    "Start at login": "Spúšťať po prihlásení",
    "Settings window…": "Okno nastavení…",
    "Settings in the web browser (for screen readers)…": "Nastavenia vo webovom prehliadači (pre čítačky obrazovky)…",
    "Open settings file": "Otvoriť súbor nastavení",
    "Stop dictation": "Ukončiť diktovanie",
    # --- the icon's tooltip and the status pop-up ---
    "Listening…": "Počúvam…",
    "Loading the speech model…": "Načítava sa rečový model…",
    "Transcribing…": "Prepisujem…",
    "Language: {language}": "Jazyk: {language}",
    "Model: {model}": "Model: {model}",
    "Downloading {model}": "Sťahuje sa {model}",
    "Waiting for the keyboard shortcut…": "Čaká sa na klávesovú skratku…",
    "Listening — {language}": "Počúvam — {language}",
    "Listening — {language} (the speech model is still loading)": "Počúvam — {language} (rečový model sa ešte načítava)",
    "About {seconds} s left": "Zostáva asi {seconds} s",
    "Almost done…": "Takmer hotovo…",
    "Taking longer than usual…": "Trvá to dlhšie ako zvyčajne…",
    "Typed": "Napísané",
    "Not typed. It is on the clipboard: paste it with {keys}.": "Nenapísané. Text je v schránke: vložte ho pomocou {keys}.",
    "It was not typed because {why}.": "Text sa nenapísal, lebo {why}.",
    "Only silence was recorded: is the microphone muted?": "Nahralo sa iba ticho: nie je mikrofón stlmený?",
    "Nothing was heard.": "Nič nebolo počuť.",
    "Dictation failed: {problem}": "Diktovanie zlyhalo: {problem}",
    "No speech model is loaded (the tray icon says why).": "Nie je načítaný žiadny rečový model (dôvod ukazuje ikona v lište).",
    # why a dictation was left on the clipboard, and what went wrong
    "the screen is locked": "obrazovka je zamknutá",
    "the computer was asleep": "počítač bol v režime spánku",
    "transcribing took too long": "prepis trval príliš dlho",
    "the paste keys could not be pressed": "klávesy na vloženie sa nedali stlačiť",
    "the app didn't take it (it may run as administrator, or not paste with Shift+Insert)":
        "aplikácia ho neprevzala (možno beží ako správca alebo nevkladá pomocou Shift+Insert)",
    "muted": "mikrofón je stlmený",
    "no speech": "nebolo počuť reč",
    "transcription error": "chyba prepisu",
    # --- notifications ---
    "Dictation settings could not be read": "Nastavenia diktovania sa nedali prečítať",
    "Dictation is running on the CPU": "Diktovanie beží na procesore",
    "The GPU could not be used, so the {model} model runs on the processor instead.":
        "Grafickú kartu nebolo možné použiť, model {model} preto beží na procesore.",
    "Dictation can't transcribe": "Diktovanie nemôže prepisovať",
    "The speech model could not be loaded: {error}": "Rečový model sa nepodarilo načítať: {error}",
    "Microphone is muted": "Mikrofón je stlmený",
    "Only silence was recorded. Check the headset's mute switch.":
        "Nahralo sa iba ticho. Skontrolujte vypínač mikrofónu na slúchadlách.",
    "Only silence was recorded. Check the headset's mute switch, and that Dictate may use the microphone "
    "(System Settings > Privacy & Security).":
        "Nahralo sa iba ticho. Skontrolujte vypínač mikrofónu na slúchadlách a či Dictate smie používať mikrofón "
        "(Systémové nastavenia > Súkromie a bezpečnosť).",
    "Dictation is stuck": "Diktovanie sa zaseklo",
    "It already restarted itself recently. Restart the computer if it stays stuck.":
        "Nedávno sa už samo reštartovalo. Ak zostane zaseknuté, reštartujte počítač.",
    "Dictation restarted on the processor": "Diktovanie sa reštartovalo na procesore",
    "The graphics card did not respond while the speech model was loading, so the model now runs on the CPU.":
        "Grafická karta neodpovedala počas načítavania rečového modelu, model preto teraz beží na procesore.",
    "Dictation restarted": "Diktovanie sa reštartovalo",
    "Transcribing got stuck, so dictation started over. The last dictation was lost.":
        "Prepis sa zasekol, diktovanie sa preto spustilo odznova. Posledný diktát sa stratil.",
    "Speech model not found": "Rečový model sa nenašiel",
    "There is no model called {model} in {folder}.": "V priečinku {folder} nie je model s názvom {model}.",
    "Downloading the {model} speech model": "Sťahuje sa rečový model {model}",
    "{size}. Dictation keeps working with the current model meanwhile.":
        "{size}. Medzitým sa diktuje so súčasným modelom.",
    "The {model} model could not be downloaded": "Model {model} sa nepodarilo stiahnuť",
    "Check the internet connection. The log has the details.":
        "Skontrolujte pripojenie na internet. Podrobnosti sú v zázname (logu).",
    "Dictation copied to the clipboard": "Diktát je skopírovaný do schránky",
    "It was not typed because {why}. Paste it with {keys}.": "Text sa nenapísal, lebo {why}. Vložte ho pomocou {keys}.",
    "Dictation shortcut was not approved": "Skratka na diktovanie nebola schválená",
    "Run 'systemctl --user restart dictate' to see GNOME's dialog again.":
        "Spustite 'systemctl --user restart dictate' a okno GNOME sa zobrazí znova.",
    "Dictation stopped": "Diktovanie sa zastavilo",
    "Dictation can't type": "Diktovanie nemôže písať",
    "{problem}; text will only be copied.": "{problem}; text sa bude iba kopírovať.",
    "Could not change starting at login": "Spúšťanie po prihlásení sa nepodarilo zmeniť",
    "The dictation key could not be set up: {error}": "Kláves na diktovanie sa nepodarilo nastaviť: {error}",
    "The dictation key could not be set up (error {code}).": "Kláves na diktovanie sa nepodarilo nastaviť (chyba {code}).",
    "Dictation key not on a Mac": "Kláves na diktovanie na Macu nie je",
    "{key} doesn't exist on a Mac keyboard, so Right Option is used. Choose another key in the settings window.":
        "{key} na klávesnici Macu nie je, používa sa preto pravý Option. Iný kláves vyberte v okne nastavení.",
    "Allow Dictate to use the keyboard": "Povoľte aplikácii Dictate používať klávesnicu",
    "In System Settings > Privacy & Security, switch Dictate on under Accessibility and under Input Monitoring.":
        "V Systémových nastaveniach > Súkromie a bezpečnosť zapnite Dictate v častiach Prístupnosť (Accessibility) "
        "a Monitorovanie vstupu (Input Monitoring).",
    # --- the settings windows ---
    "Dictate settings": "Nastavenia diktovania",
    "Dictation is running.": "Diktovanie beží.",
    "Dictation is not running.": "Diktovanie nebeží.",
    "Model: {model}.": "Model: {model}.",
    "Open these settings in the web browser (for screen readers)":
        "Otvoriť tieto nastavenia vo webovom prehliadači (pre čítačky obrazovky)",
    "Language you dictate in": "Jazyk diktovania",
    "Typing": "Písanie",
    "Type instantly, correcting as it goes": "Písať okamžite, s priebežnými opravami",
    "Main model": "Hlavný model",
    "Main model (runs on the graphics card)": "Hlavný model (beží na grafickej karte)",
    "Main model (runs on the processor)": "Hlavný model (beží na procesore)",
    "Types while you speak and recognizes the language; does what the models below don't":
        "Píše počas hovorenia a rozpoznáva jazyk; robí, čo modely nižšie nerobia",
    ", downloading {progress}": ", sťahuje sa {progress}",
    ", download {size}": ", stiahnuť {size}",
    "Run on": "Kde beží",
    "Graphics card (GPU): none can be used": "Grafická karta (GPU): žiadnu nemožno použiť",
    "Dictation key": "Kláves na diktovanie",
    "Hold: {key}. Change it…": "Držať: {key}. Zmeniť…",
    "Use numpad Del again": "Znova používať numerický Del",
    "Pop-up at the bottom of the screen": "Okienko dole na obrazovke",
    "Pop-up at the top of the screen": "Okienko hore na obrazovke",
    "Where the pop-up shows": "Kde sa okienko zobrazuje",
    "Clicking the tray icon opens this window": "Kliknutie na ikonu v lište otvorí toto okno",
    "Size {size}×": "Veľkosť {size}×",
    "Text size": "Veľkosť písma",
    "Colours": "Farby",
    "Yellow on black": "Žltá na čiernej",
    "White on black": "Biela na čiernej",
    "Black on white": "Čierna na bielej",
    "Black on yellow": "Čierna na žltej",
    "Starting": "Spúšťanie",
    "Start dictation at login": "Spúšťať diktovanie po prihlásení",
    "Open the settings file": "Otvoriť súbor nastavení",
    "Close this window": "Zavrieť toto okno",
    "Dictation is stopping.": "Diktovanie sa ukončuje.",
    "Could not change starting at login: {error}": "Spúšťanie po prihlásení sa nepodarilo zmeniť: {error}",
    "Press the new dictation key, or a key combination like Ctrl+Alt+D, now. Esc cancels.":
        "Teraz stlačte nový kláves na diktovanie alebo kombináciu ako Ctrl+Alt+D. Esc zruší.",
    "The key was not changed.": "Kláves sa nezmenil.",
    "{problem} Try another key or combination; Esc cancels.": "{problem} Skúste iný kláves alebo kombináciu; Esc zruší.",
    "New key: {key}. Dictation restarts to use it.": "Nový kláves: {key}. Diktovanie sa reštartuje, aby ho používalo.",
    "New key: {key}. Dictation restarts to use it, and your desktop asks you to approve it.":
        "Nový kláves: {key}. Diktovanie sa reštartuje, aby ho používalo, a pracovné prostredie vás požiada o schválenie.",
    "AltGr can be the dictation key only on its own": "AltGr môže byť klávesom na diktovanie iba samostatne",
    "{key} can't be the dictation key": "{key} nemôže byť klávesom na diktovanie",
    "This page has ended. Open the settings again from the Dictate menu.":
        "Táto stránka skončila. Nastavenia znova otvoríte z ponuky Dictate.",
    "The browser keeps this tab open: close it with Ctrl+W (Cmd+W on a Mac).":
        "Prehliadač túto kartu nechá otvorenú: zavrite ju pomocou Ctrl+W (na Macu Cmd+W).",
    # --- updates ---
    "Check for updates": "Skontrolovať aktualizácie",
    "Checking for updates…": "Hľadajú sa aktualizácie…",
    "Install the update from {date}…": "Nainštalovať aktualizáciu z {date}…",
    "Install the update from {date}": "Nainštalovať aktualizáciu z {date}",
    "Updates": "Aktualizácie",
    "Dictation is up to date": "Diktovanie je aktuálne",
    "This is the newest version (from {date}).": "Toto je najnovšia verzia (z {date}).",
    "An update is available": "Je dostupná aktualizácia",
    "The version from {date}: {summary}. Install it from the menu: Install the update.":
        "Verzia z {date}: {summary}. Nainštalujete ju v ponuke: Nainštalovať aktualizáciu.",
    "The version from {date} is available: {summary}.": "Je dostupná verzia z {date}: {summary}.",
    "Could not check for updates": "Aktualizácie sa nepodarilo skontrolovať",
    "GitHub can't be reached: is the computer online?": "GitHub je nedostupný: je počítač pripojený na internet?",
    "GitHub allows only a few checks an hour from one address; try again later.":
        "GitHub povoľuje z jednej adresy len niekoľko kontrol za hodinu; skúste to neskôr.",
    "GitHub's answer was unexpected ({detail}).": "GitHub odpovedal neočakávane ({detail}).",
    "The update could not start": "Aktualizáciu sa nepodarilo spustiť",
    "The update could not start: {error}": "Aktualizáciu sa nepodarilo spustiť: {error}",
    "Updating dictation": "Diktovanie sa aktualizuje",
    "Dictation stops for about a minute while the new version is installed, and says when it is back.":
        "Diktovanie sa asi na minútu zastaví, kým sa nainštaluje nová verzia, a dá vedieť, keď bude späť.",
    "Updating: dictation stops for about a minute while the new version is installed, and says when it is back.":
        "Aktualizuje sa: diktovanie sa asi na minútu zastaví, kým sa nainštaluje nová verzia, a dá vedieť, keď bude späť.",
    "Dictation is updated": "Diktovanie je aktualizované",
    "This is now the version from {date}.": "Teraz beží verzia z {date}.",
    "The update did not work": "Aktualizácia sa nepodarila",
    "The previous version is back. The reason: {error}. Details are in {log}.":
        "Predchádzajúca verzia je späť. Dôvod: {error}. Podrobnosti sú v {log}.",
    "the new version didn't start": "nová verzia sa nespustila",
    # --- keys ---
    "numpad Del": "numerický Del", "numpad 0": "numerická 0", "numpad Enter": "numerický Enter",
    "Page Up": "Page Up", "Page Down": "Page Down", "Scroll Lock": "Scroll Lock", "Print Screen": "Print Screen",
    "Space": "Medzerník", "Menu key": "kláves Menu", "AltGr": "AltGr", "fn": "fn",
    "Right Ctrl": "pravý Ctrl", "Left Ctrl": "ľavý Ctrl", "Right Alt": "pravý Alt", "Left Alt": "ľavý Alt",
    "Right Shift": "pravý Shift", "Left Shift": "ľavý Shift", "Right Super": "pravý Super", "Left Super": "ľavý Super",
    "Right Option": "pravý Option", "Left Option": "ľavý Option", "Right Command": "pravý Command",
    "Left Command": "ľavý Command", "Right Control": "pravý Control", "Left Control": "ľavý Control",
    "Right Windows key": "pravý kláves Windows", "Left Windows key": "ľavý kláves Windows",
    "not a key: {text!r}": "nie je to kláves: {text!r}",
    "{name!r} is not a modifier (Ctrl, Alt/Option, Shift, Super/Win/Cmd)":
        "{name!r} nie je modifikátor (Ctrl, Alt/Option, Shift, Super/Win/Cmd)",
    "unknown key {key!r}": "neznámy kláves {key!r}",
    "a combination needs a key that isn't a modifier, like Ctrl+Alt+D":
        "kombinácia potrebuje kláves, ktorý nie je modifikátor, napríklad Ctrl+Alt+D",
    "{key} on its own could no longer be typed; add a modifier, like {example}":
        "{key} by sa samostatne už nedal napísať; pridajte modifikátor, napríklad {example}",
    "{key} has no Windows equivalent": "{key} nemá vo Windows obdobu",
    "{key} has no Mac equivalent": "{key} nemá na Macu obdobu",
    # --- speech models, hardware, start at login ---
    "fastest; rough English, no Slovak": "najrýchlejší; hrubá angličtina, bez slovenčiny",
    "fast; good English, no Slovak": "rýchly; dobrá angličtina, bez slovenčiny",
    "very good English, poor Slovak": "veľmi dobrá angličtina, slabá slovenčina",
    "excellent English, fair Slovak; slow without a GPU": "výborná angličtina, slušná slovenčina; bez grafickej karty pomalý",
    "best, also for Slovak; slow without a GPU": "najlepší, aj pre slovenčinu; bez grafickej karty pomalý",
    "as accurate as large-v3-turbo, 1.5x slower; graphics card only":
        "rovnako presný ako large-v3-turbo, 1,5× pomalší; iba na grafickej karte",
    "slightly better English, worse Slovak, 1.5x slower; graphics card only":
        "o niečo lepšia angličtina, horšia slovenčina, 1,5× pomalší; iba na grafickej karte",
    "the most accurate; graphics card": "najpresnejší; grafická karta",
    "very good; slow without a GPU": "veľmi dobrý; bez grafickej karty pomalý",
    "good, and fast enough for a processor": "dobrý a dosť rýchly pre procesor",
    "fast; makes more mistakes": "rýchly; robí viac chýb",
    "None: the main model does Slovak too": "Žiadny: slovenčinu robí aj hlavný model",
    "Writes Slovak after you release the key (not while typing as you speak)":
        "Píše slovenčinu po pustení klávesu (nie pri písaní počas hovorenia)",
    "Model for Slovak: {model}": "Model pre slovenčinu: {model}",
    "none": "žiadny",
    "Model for Slovak": "Model pre slovenčinu",
    "no, the speech model alone": "nie, len rečový model",
    "Also a model fine-tuned for Slovak (by KInIT)? It makes about a third of the mistakes in Slovak, and is used only "
    "when you speak Slovak.":
        "Aj model doladený pre slovenčinu (od KInIT)? V slovenčine robí asi tretinu chýb a používa sa len vtedy, keď "
        "hovoríte po slovensky.",
    "the most accurate English": "najpresnejšia angličtina",
    "smaller and faster, still very good English": "menší a rýchlejší, stále veľmi dobrá angličtina",
    "{model} + {software} of software": "{model} + {software} softvéru",
    "None: the main model does English too": "Žiadny: angličtinu robí aj hlavný model",
    "Writes English after you release the key (not while typing as you speak)":
        "Píše angličtinu po pustení klávesu (nie pri písaní počas hovorenia)",
    "needs an NVIDIA graphics card": "potrebuje grafickú kartu NVIDIA",
    "Model for English: {model}": "Model pre angličtinu: {model}",
    "Model for English": "Model pre angličtinu",
    "Qwen could not start": "Qwen sa nepodarilo spustiť",
    "Qwen stopped working": "Qwen prestal fungovať",
    "English goes to the main model instead. The reason: {error}":
        "Angličtinu namiesto neho prepisuje hlavný model. Dôvod: {error}",
    "no usable GPU": "žiadna použiteľná grafická karta",
    "{gpu}; {cores}-core CPU; {ram} GB RAM": "{gpu}; {cores}-jadrový procesor; {ram} GB RAM",
    "laptop": "notebook",
    "systemd user service": "používateľská služba systemd",
    "autostart entry": "položka automatického spustenia",
    "LaunchAgent": "LaunchAgent",
    "shortcut in the Startup folder": "odkaz v priečinku Po spustení",
    "on ({how})": "zapnuté ({how})",
    "off ({how}); the menu's Start at login switches it on": "vypnuté ({how}); zapnete ho v ponuke: Spúšťať po prihlásení",
    "could not set up start-at-login: {error}": "spúšťanie po prihlásení sa nepodarilo nastaviť: {error}",
    # --- the installers' questions (dictate.py --setup) ---
    "This computer: {hardware}": "Tento počítač: {hardware}",
    "Your current choices: language {language}, model {model}, key {key}.":
        "Vaše súčasné voľby: jazyk {language}, model {model}, kláves {key}.",
    "keep them": "ponechať ich",
    "choose again": "vybrať znova",
    "recommended": "odporúčané",
    "current": "súčasné",
    "Type 1-{last} and Enter, or just Enter for {default}: ": "Napíšte 1-{last} a Enter, alebo len Enter pre {default}: ",
    "Please type one of the numbers, or just press Enter.": "Napíšte jedno z čísel, alebo len stlačte Enter.",
    "{name}={value!r} is not one of: {choices}": "{name}={value!r} nie je jedna z možností: {choices}",
    "Which language will you dictate?": "V akom jazyku budete diktovať?",
    "English": "Angličtina",
    "Slovak (Slovenčina)": "Slovenčina",
    "Both: English or Slovak, detected each time": "Oboje: angličtina alebo slovenčina, rozpozná sa zakaždým",
    "Which speech model? It runs on the graphics card; bigger models are more accurate but slower.":
        "Ktorý rečový model? Beží na grafickej karte; väčšie modely sú presnejšie, ale pomalšie.",
    "Which speech model? It runs on the processor; bigger models are more accurate but slower.":
        "Ktorý rečový model? Beží na procesore; väčšie modely sú presnejšie, ale pomalšie.",
    "Which speech model? It runs on the processor (no usable graphics card); bigger models are more accurate but slower.":
        "Ktorý rečový model? Beží na procesore (použiteľná grafická karta chýba); väčšie modely sú presnejšie, ale "
        "pomalšie.",
    "(suggested for this computer)": "(navrhovaný pre tento počítač)",
    "your own model": "váš vlastný model",
    "Which key do you hold to dictate?": "Ktorý kláves budete pri diktovaní držať?",
    "Which key do you hold to dictate? (On Wayland the desktop then shows its own dialog to approve or change it.)":
        "Ktorý kláves budete pri diktovaní držať? (Na Waylande potom pracovné prostredie zobrazí vlastné okno, kde "
        "ho schválite alebo zmeníte.)",
    "Right Option (⌥)": "pravý Option (⌥)",
    "Right Command (⌘)": "pravý Command (⌘)",
    "numpad . (a keyboard with a numpad)": "numerická . (klávesnica s numerickou časťou)",
    "numpad Del / .   (keyboards with a numpad)": "numerický Del / .   (klávesnice s numerickou časťou)",
    "Right Ctrl        (laptops; it still works in shortcuts)": "pravý Ctrl        (notebooky; v skratkách funguje naďalej)",
    "another key or a combination, typed in (e.g. F13, Ctrl+Alt+D)":
        "iný kláves alebo kombinácia, napíšete ich (napr. F13, Ctrl+Alt+D)",
    "Type the key or combination (Enter for the suggestion): ": "Napíšte kláves alebo kombináciu (Enter pre návrh): ",
    "Large text, for low vision? (Size and colours can be changed later in the settings window.)":
        "Veľké písmo pre slabozrakých? (Veľkosť a farby sa dajú neskôr zmeniť v okne nastavení.)",
    "no": "nie",
    "a big status panel while dictating": "veľký stavový panel počas diktovania",
    "the big panel, and a big settings window when you click the tray icon":
        "veľký panel a veľké okno nastavení po kliknutí na ikonu v lište",
    "Keeping your settings file {path}": "Váš súbor nastavení {path} zostáva",
    "Created the settings file {path}": "Vytvoril sa súbor nastavení {path}",
    "The {model} model is already downloaded": "Model {model} je už stiahnutý",
    "Downloading the {model} model ({size})…": "Sťahuje sa model {model} ({size})…",
    "Dictation stops while the model is checked; the installer starts it again.":
        "Diktovanie sa počas kontroly modelu zastaví; inštalátor ho potom znova spustí.",
    "Loading the model once to check it:": "Model sa raz načíta na kontrolu:",
    "the GPU could not be used, so the model runs on the CPU (the log has the reason)":
        "grafickú kartu nebolo možné použiť, model teda beží na procesore (dôvod je v zázname)",
    # --- install.sh (printf: %s) ---
    "Program files": "Programové súbory",
    "Downloading uv %s (Python package manager, kept inside %s)": "Sťahuje sa uv %s (správca balíkov Pythonu, uložený v %s)",
    "Private Python %s (separate from the system Python, so system upgrades leave it alone)":
        "Vlastný Python %s (oddelený od systémového, aktualizácie systému ho teda neovplyvnia)",
    "Python packages": "Balíky Pythonu",
    "    CTranslate2 %s for ROCm (downloads about 280 MB, keeps 45 MB)": "    CTranslate2 %s pre ROCm (stiahne asi 280 MB, ponechá 45 MB)",
    "    ROCm %s runtime with device code for %s (downloads about 1 GB the first time)":
        "    ROCm %s s kódom pre %s (prvýkrát stiahne asi 1 GB)",
    "Language, speech model and key (Enter takes the suggestion)": "Jazyk, rečový model a kláves (Enter prijme návrh)",
    "Dictate.app (in ~/Applications; macOS asks for permissions in its name)":
        "Dictate.app (v ~/Applications; macOS žiada o povolenia v jej mene)",
    "    keeping %s": "    ponecháva sa %s",
    "Start at login: LaunchAgent (it opens Dictate.app)": "Spúšťanie po prihlásení: LaunchAgent (otvára Dictate.app)",
    "Launchers (%s.desktop: also the identity the desktop stores the shortcut under)":
        "Spúšťače (%s.desktop: aj identita, pod ktorou si pracovné prostredie pamätá skratku)",
    "Start at login: systemd user service": "Spúšťanie po prihlásení: používateľská služba systemd",
    "Start at login: autostart entry (this desktop runs without a systemd graphical session)":
        "Spúšťanie po prihlásení: položka automatického spustenia (toto prostredie nemá grafickú reláciu systemd)",
    "Checking the installation": "Kontrola inštalácie",
    "Note: this user has no access to the AMD GPU yet (/dev/kfd), so it runs on the CPU until you allow it.":
        "Poznámka: tento používateľ zatiaľ nemá prístup ku grafickej karte AMD (/dev/kfd), beží teda na procesore, "
        "kým ho nepovolíte.",
    "      See README: Troubleshooting, \"AMD GPU not used\".": "      Pozrite README: Troubleshooting, \"AMD GPU not used\".",
    "Done. macOS now asks to allow Dictate to use the microphone, Accessibility and Input Monitoring:":
        "Hotovo. macOS teraz požiada o povolenia pre Dictate: mikrofón, Prístupnosť (Accessibility) a Monitorovanie "
        "vstupu (Input Monitoring):",
    "allow all three (System Settings > Privacy & Security), then hold the dictation key, speak, release.":
        "povoľte všetky tri (Systémové nastavenia > Súkromie a bezpečnosť), potom podržte kláves na diktovanie, "
        "hovorte a pustite.",
    "Done. Hold the dictation key, speak, release. The first start asks your desktop to approve the key (Wayland).":
        "Hotovo. Podržte kláves na diktovanie, hovorte a pustite. Pri prvom spustení vás pracovné prostredie požiada "
        "o schválenie klávesu (Wayland).",
    "The installer has finished: you can close this window.": "Inštalácia skončila: toto okno môžete zavrieť.",
    # --- install.ps1 (-f: {0}) ---
    "Note: {0} has no code in CTranslate2's Windows GPU build, so dictation runs on the processor.":
        "Poznámka: {0} nemá kód vo verzii CTranslate2 pre grafické karty vo Windows, diktovanie teda beží na procesore.",
    "Graphics: {0}  ->  {1}": "Grafika: {0}  ->  {1}",
    "the processor (CPU)": "procesor (CPU)",
    "Stopping a running copy (Windows keeps its files in use)": "Zastavuje sa bežiaca kópia (Windows drží jej súbory otvorené)",
    "Downloading uv {0} (Python package manager, kept inside {1})": "Sťahuje sa uv {0} (správca balíkov Pythonu, uložený v {1})",
    "Private Python {0} (independent of any other Python on this PC)": "Vlastný Python {0} (nezávislý od iných Pythonov na tomto PC)",
    "    CTranslate2 {0} for ROCm (downloads about 140 MB)": "    CTranslate2 {0} pre ROCm (stiahne asi 140 MB)",
    "    ROCm {0} runtime with GPU code for {1} (downloads about 1 GB the first time)":
        "    ROCm {0} s kódom pre {1} (prvýkrát stiahne asi 1 GB)",
    "Start menu shortcuts": "Odkazy v ponuke Štart",
    "Start at login: a shortcut in the Startup folder": "Spúšťanie po prihlásení: odkaz v priečinku Po spustení",
    "Starting dictation": "Spúšťa sa diktovanie",
    "Done. Hold the dictation key, speak, release: the text is typed where the cursor is.":
        "Hotovo. Podržte kláves na diktovanie, hovorte a pustite: text sa napíše tam, kde je kurzor.",
    "The microphone icon in the taskbar's notification area has the menu (you may need to drag it out of the ^ overflow).":
        "Ponuka je pod ikonou mikrofónu v oblasti oznámení na paneli úloh (možno ju treba vytiahnuť zo skrytých ikon ^).",
}
