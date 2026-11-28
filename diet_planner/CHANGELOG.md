# Zmiany

## 1.12.1
- Tłumaczenie: obcy tytuł zwrócony przez model bez zmian nie jest już liczony jako przetłumaczony. W Ustawieniach → AI jest lista takich tytułów i przycisk „Przetłumacz tytuły ponownie” (tytuły z 1.12.0 oznaczają się same).
- Mocniejsze polecenie tłumaczenia tytułów (z przykładami); nazwy własne potraw zostają.
- Nazwy produktów nie zmieniają się już same – propozycje polskich nazw do zatwierdzenia w zakładce Produkty („Zmień”, „Inna nazwa…”, „Zostaw”). Zapożyczenia (tortilla, passata, wrap, tofu…) nie są proponowane.

## 1.12.0
- Baza produktów i przepisów w SQLite (`library/diet.db`) – składniki powiązane z produktami; dane z plików JSON przenoszą się same przy pierwszym starcie (stare pliki zostają jako `*.json.migrated`).
- Nowa zakładka Przepisy → Produkty: wyszukiwarka, działy, karta produktu z listą przepisów, zmiana nazwy (przenosi się na przepisy), scalanie duplikatów i usuwanie nieużywanych.
- Szukanie przepisów po produktach („Mam w lodówce…”) i klikalne składniki w karcie przepisu.
- Tłumaczenie przepisów na polski (Ustawienia → AI) – tytuły i treść, bez nazw własnych; import zawsze po polsku.
- Poprawka: szacowany koszt w karcie „Instrukcje” znów się wyświetla.

## 1.11.1
- Panel „Dieta” widoczny dla wszystkich użytkowników HA, nie tylko administratorów (`panel_admin: false`, jak w wersji lokalnej).

## 1.11.0
- Czwarty dostawca AI: OpenAI (GPT) – klucz `openai_api_key`, modele np. `openai:gpt-6.2-sol`.
- Wyczerpany limit wydatków u dostawcy od razu przełącza na model zapasowy.

## 1.10.0
- Modele AI od trzech dostawców: Anthropic (Claude), Google (Gemini) i xAI (Grok).
- Wybór modelu i modelu zapasowego osobno dla każdej funkcji (Ustawienia → AI), przycisk „Test”.
- Zużycie AI z podziałem na dostawców.

## 1.9.0
- Instalacja i aktualizacje z repozytorium dodatków (bez kopiowania plików na serwer).
- Dane startowe to tylko szablony: profile „Osoba 1/2”, domyślne posiłki i podstawowe przepisy.
- Obraz bazowy `python:3.12-alpine` z Docker Hub (bez `build.yaml` i bez ghcr.io).

## 1.8.2
- Lista zakupów: „dokup +X”, gdy po zmianie planu trzeba więcej już kupionego produktu.
- Ilości bez „.0”.

## 1.8.1
- Limit czasu niedzielnej sesji meal prepu (1 h / 1,5 h / 2 h / 3 h).

## 1.8.0
- Zakładki „Dziś” i „Tydzień”, prostsza lista zadań meal prepu, przepisy jako lista.
