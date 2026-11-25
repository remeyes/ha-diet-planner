# Zmiany

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
