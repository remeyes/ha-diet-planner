# Diet Planner – dodatek do Home Assistant

Panel **Dieta** w menu bocznym Home Assistant. Zawiera:
- **plan tygodnia** dzień po dniu, z porcjami dla każdej osoby;
- **listę zakupów** z odhaczaniem, zsynchronizowaną między telefonami;
- **przepisy** z możliwością dodawania własnych;
- **profile** z kalkulatorem zapotrzebowania;
- **ustawienia** posiłków i gotowania.

Logowanie i dostęp z zewnątrz zapewnia Home Assistant (Ingress). Jeśli łączysz się z HA przez Tailscale, panel działa tak samo jak reszta HA. Nie trzeba otwierać żadnego dodatkowego portu.

## Instalacja

1. W HA otwórz **Ustawienia → Aplikacje → Zainstaluj aplikację** (sklep). Z menu **⋮** wybierz **Repozytoria**.
2. Dodaj adres repozytorium z dodatkiem (adres GitHub, np. `https://github.com/<użytkownik>/<repozytorium>`) i zamknij okno.
3. Odśwież stronę – na liście pojawi się **Diet Planner**. Kliknij **Zainstaluj**. Pierwsze budowanie trwa kilka minut.
4. W zakładce **Konfiguracja** wpisz klucz Claude API w `api_key` (opcjonalnie – bez niego działa wszystko poza AI).
5. Włącz **Pokaż na pasku bocznym** i kliknij **Uruchom**. W menu HA pojawi się pozycja **Dieta**.

## Modele AI (Anthropic, Google, xAI, OpenAI)

Funkcje AI mogą korzystać z modeli czterech dostawców: **Anthropic (Claude)**, **Google (Gemini)**, **xAI (Grok)** i **OpenAI (GPT)**.

1. Załóż klucz u wybranych dostawców (wystarczy jeden): console.anthropic.com, aistudio.google.com, console.x.ai, platform.openai.com.
2. W HA otwórz **Aplikacje → Diet Planner → Konfiguracja** i wpisz klucze w polach `api_key` (Anthropic), `google_api_key`, `xai_api_key`, `openai_api_key`. Zapisz i uruchom dodatek ponownie.
3. W aplikacji: **Ustawienia → AI → Modele AI** wybierz model dla każdej funkcji (agent diety, przepis z tekstu, import książek, instrukcje i meal prep, szybki zamiennik, zdjęcie lodówki, błonnik) i opcjonalnie **model zapasowy**. Przycisk **Test** sprawdza, czy model odpowiada.

Model zapisujesz jako `dostawca:model`, np. `anthropic:claude-opus-5-5`, `google:gemini-3.8-flash`, `xai:grok-4.7`, `openai:gpt-6.2-sol`. Gdy główny dostawca nie odpowiada albo brakuje klucza, aplikacja sama użyje modelu zapasowego. Kalorie, porcje i alergie zawsze liczy i pilnuje kod aplikacji – niezależnie od modelu.

Klucze zostają na serwerze i nie trafiają do przeglądarki. Zdjęcia lodówki są przed wysłaniem zmniejszane i trafiają do wybranego dostawcy.

## Agenci AI

Model dla każdego agenta wybierasz w **Ustawienia → AI** (patrz wyżej).

- **Agent diety:** przycisk **Ułóż plan → Ułóż z agentem**. Dobiera dania z biblioteki pod Wasze preferencje, oceny 👍/👎, historię, sezon i życzenia wpisane w okienku. Alergie i produkty z „Nie lubi” pilnuje kod aplikacji, a porcje, kalorie i zakupy też liczy aplikacja. Uzasadnienie wyboru zobaczysz w karcie „Dlaczego taki plan”.
- **Agent przepisów:** **Przepisy → ✨ Z tekstu** albo import książki. Rozpoznaje składniki, przelicza je na gramy na porcję i dopasowuje do bazy produktów. Brakujące produkty dodaje z szacowanymi wartościami, oznaczonymi jako „szacunek”. Szkice trafiają do zakładki **Do przejrzenia**, gdzie je zatwierdzasz.
- **Kaloryczność:** liczy ją jeden moduł aplikacji. Wynik ma każdy przepis, a plik `library/nutrition.md` zawiera tabelę kcal/B/W/T, którą może czytać też skill Claude.

### Zakładki Dziś i Tydzień

- **Dziś** (ekran startowy): posiłki na dzisiaj z porcjami dla każdej osoby, przypomnienia o rozmrażaniu i sesji meal prepu. W niedzielę pojawia się też sesja na przyszły tydzień.
- **Tydzień:** przegląd 7 dni, karta „Gotowanie w tym tygodniu” i przełączanie tygodni strzałkami ‹ ›. Stuknij dzień, żeby zobaczyć szczegóły.

### Meal prep na cały tydzień

Tryb gotowania wybierasz osobno dla każdego tygodnia w oknie **Ułóż plan**: bez meal prepu, obiady co 2 dni albo cały tydzień w niedzielę. W trybie tygodniowym:
- **Lista zadań na niedzielę** (Tydzień → Gotowanie w tym tygodniu) ma 3 kroki:
  1. **Na start:** piekarnik i garnki, które pracują same.
  2. **W międzyczasie:** patelnia i krojenie.
  3. **Pakowanie:** pojemniki według dni.
  
  Czas sesji uwzględnia, że garnki i piekarnik pracują w tle. Ten sam ryż albo kaszę do kilku dań gotujesz raz.
- **Świeżość:** etap, który nie dotrwa w lodówce, trafia do zamrażarki, jeśli się mrozi. Wtedy dzień wcześniej pojawia się przypomnienie „🧊 przenieś do lodówki”. Etap, który się nie mrozi, robisz na krótkiej sesji w środę wieczorem albo na świeżo w dniu posiłku.
- **W dniu posiłku:** karta pokazuje „⚡ Dziś na świeżo”, czyli co wziąć z lodówki i co dorobić, np. usmażyć mięso albo odgrzać ryż i sos.

Etapy każdego przepisu ustala agent przepisów. Rozpisanie istniejących przepisów uruchomisz w **Ustawienia → Instrukcje, porcjowanie i meal prep**. Do tego czasu przepis „na zapas” jest traktowany ostrożnie: jako całość, 2 dni w lodówce, bez mrożenia. Pozostałe przepisy robisz na świeżo.

### Podział porcji między osoby

Każdy przepis ma sposób porcjowania, który ustala agent przepisów:
- **🍲 Wspólny garnek** (zupa, gulasz, zapiekanka): ugotuj całość i podziel na najwyżej 5 równych części, np. „Osoba 1: 3, Osoba 2: 2”. Przy meal prepie rozłóż całość na pojemniki, po jednym na dzień.
- **🥞 W sztukach** (placki, pulpety, kanapki): plan podaje całkowitą liczbę sztuk dla każdej osoby, np. „13 szt.: Osoba 1: 7, Osoba 2: 6”.
- **Każdy osobno** (owsianka, jogurt, przekąski): każdy dostaje swoje gramatury. Te posiłki wyrównują różnicę kcal z garnków, więc suma dnia trzyma się celu.

Przepisy, które agent jeszcze nie sklasyfikował, mają oznaczenie „auto” (tryb zgadnięty po nazwie). Klasyfikację uruchomisz w **Ustawienia → Instrukcje, porcjowanie i meal prep**.

### Makro i podobne dania

- **Karta przepisu:** przy każdym składniku widać kcal, białko, węglowodany, tłuszcze i błonnik, do tego sumę, wartości na 100 g i pasek rozkładu energii. Brakujący błonnik uzupełnisz przyciskiem **Uzupełnij błonnik (AI)**: w karcie przepisu albo w **Ustawienia → Agenci AI**. Te wartości są szacunkowe.
- **Zamień danie:** lista jest ułożona od dania o najbardziej podobnym makro. Przy każdym daniu widać różnicę B/W/T w gramach dla każdej osoby, przy tej samej kaloryczności porcji.

### Instrukcje przepisów

Każdy przepis ma trzy części:
- **🔥 Przed gotowaniem:** co zrobić najpierw, np. rozgrzać piekarnik, namoczyć strączki, wyjąć mięso z lodówki;
- **Przygotowanie:** numerowane kroki z czasem, temperaturą i oznaką gotowości, bez gramatur, bo ilości są w tabeli składników. Kliknięcie kroku go odhacza;
- **🧊 Przechowywanie i odgrzewanie:** tylko przy daniach na zapas.

Agent przepisów tworzy te części dla nowych przepisów sam. Przepisy ze starym, skróconym opisem rozpiszesz w **Ustawienia → Instrukcje przepisów**:
1. Wybierz model: tańszy albo dokładniejszy.
2. Kliknij **Oszacuj koszt**, a potem **Rozpisz instrukcje**.

Składniki i kalorie się nie zmieniają. Kopia sprzed zmiany trafia do `library/recipes.before-instructions.json`.

### Książki kucharskie

1. Wrzuć pliki PDF (z tekstem), EPUB albo MOBI/AZW3 do `addon_configs/xxxxxxxx_diet_planner/books/` przez udział Samba. Możesz też wgrać plik w **Przepisy → Książki**.
2. Kliknij **Oszacuj koszt**, a potem **Importuj przepisy**. Import działa w tle, można go wstrzymać i wznowić.
3. Przejrzyj szkice w zakładce **Do przejrzenia**.

Ważne informacje:
- **Treść książki** trafia we fragmentach do Claude API.
- **Model importu** wybierasz w **Ustawienia → AI** (funkcja „Import książek kucharskich”), domyślnie tani `claude-haiku-5-5`.
- **Pliki z DRM** (np. Kindle, Legimi) i **skany bez warstwy tekstu** nie dadzą się odczytać.
- **Biblioteka przepisów jest prywatna** i służy tylko Tobie.

## Dane

Pierwsze uruchomienie tworzy **szablony**: dwa profile („Osoba 1”, „Osoba 2”) do uzupełnienia w **Ustawienia → Osoby**, domyślne ustawienia posiłków i podstawową bazę ok. 30 przepisów. Dodatek nie zawiera żadnych danych osobowych.

Wszystko, co wprowadzisz, leży w katalogu dodatku `addon_configs/…_diet_planner` (dostęp przez udział Samba `addon_configs`):

- `profiles/*.md`, `config/diet-settings.md`: profile i ustawienia (nazwa pliku = osoba; możesz dodać kolejne);
- `plans/RRRR-Wtt.json` i `.md`: plany tygodni i listy zakupów;
- `library/`: przepisy, produkty, szkice i postęp importu książek;
- `books/`: Twoje książki kucharskie.

Aktualizacja dodatku **nigdy nie nadpisuje** tych plików. Kopię zapasową robi zwykły backup Home Assistant.

## Aktualizacja

Gdy w repozytorium pojawi się nowa wersja, HA pokaże ją w **Ustawienia → Aktualizacje** (jak przy oficjalnych dodatkach). Kliknij **Aktualizuj**. Jeśli nie widać jej od razu: **Aplikacje → sklep → ⋮ → Sprawdź aktualizacje**.

## Rozwiązywanie problemów

- **Panel jest pusty albo pokazuje błąd:** otwórz zakładkę **Dziennik** dodatku i sprawdź komunikaty.
- **Strona zwraca „Dostęp tylko przez Home Assistant”:** dodatek przyjmuje połączenia wyłącznie przez panel HA. To celowe zabezpieczenie.
