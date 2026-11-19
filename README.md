# Guitar and Bass AI Smart Remover

A Windows app that removes the guitar or bass from a song locally, shows a playable tab with a 3D fretting hand, and lets you loop and slow down any part. Separation runs offline with Demucs and Basic Pitch; no API keys, accounts, or cloud service. Generated tracks are MP3, with a Save button for the version with the instrument removed.

## Install on Windows

1. Open [Releases](https://github.com/Closegamer/Guitar-and-Bass-Remover/releases/latest) and download `GuitarAndBassAISmartRemoverSetup.exe`.
2. Run the setup. It installs for the current user only, so it does not need admin rights.
3. The app starts when the installer finishes and opens in your browser.
4. If Windows SmartScreen shows a warning, choose **More info** then **Run anyway**. The build is not code-signed.

The full project source is also available as a ZIP from [Code > Download ZIP](https://github.com/Closegamer/Guitar-and-Bass-Remover/archive/refs/heads/main.zip). The ZIP contains the source and build scripts; the ready installer is in Releases.

## How to use

1. Choose **Guitar** or **Bass** in the header, then load an audio file (MP3, WAV, FLAC, M4A, OGG).
2. Wait for the first cut. It can take a minute; the tab appears when the instrument stem is ready.
3. Use **Full mix**, **No guitar** (or **No bass**) and **Guitar only** (or **Bass only**) to switch what you hear.
4. Drag on the waveform to loop a part. Space plays or pauses, the arrow keys seek, and the speed slider slows down without changing pitch.
5. **Save no guitar** or **Save no bass** downloads the backing track as an MP3.
6. **Clear saved songs** deletes every loaded song and its generated files.

Saved songs and the separation model live in `%LOCALAPPDATA%\GuitarAndBassAISmartRemover`. The uninstaller stops the app and removes that folder, including saved songs.

## Build from source

Requirements: Windows 10 or 11 x64, Python 3.12 x64, and [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The setup is written to `dist\GuitarAndBassAISmartRemoverSetup.exe`. For development, run `python server.py` and open http://127.0.0.1:8765. A CUDA GPU is optional but makes separation much faster.

## Русский

Windows-приложение, которое локально убирает гитару или бас из песни, показывает табулатуру и 3D-постановку руки, позволяет зацикливать и замедлять любой фрагмент. Разделение работает офлайн на Demucs и Basic Pitch, без API-ключей, аккаунтов и облачных сервисов. Готовые треки сохраняются в MP3, а кнопка Save скачивает версию без выбранного инструмента.

### Установка

1. Откройте [Releases](https://github.com/Closegamer/Guitar-and-Bass-Remover/releases/latest) и скачайте `GuitarAndBassAISmartRemoverSetup.exe`.
2. Запустите установщик. Он ставит приложение только для текущего пользователя, права администратора не нужны.
3. Приложение запустится после установки и откроется в браузере.
4. Если Windows SmartScreen показывает предупреждение, нажмите **Подробнее**, затем **Выполнить в любом случае**. Сборка не подписана.

Полный исходный код проекта можно скачать архивом: [Code > Download ZIP](https://github.com/Closegamer/Guitar-and-Bass-Remover/archive/refs/heads/main.zip). В архиве исходники и скрипты сборки, готовый установщик лежит в Releases.

### Как пользоваться

1. Выберите **Guitar** или **Bass** в шапке и загрузите аудиофайл (MP3, WAV, FLAC, M4A, OGG).
2. Дождитесь первого разделения. Это может занять минуту; табулатура появится, когда дорожка инструмента будет готова.
3. Переключайте **Full mix**, **No guitar** (или **No bass**) и **Guitar only** (или **Bass only**), чтобы менять звук.
4. Протяните мышью по волновой форме, чтобы зациклить фрагмент. Пробел включает и ставит на паузу, стрелки перематывают, ползунок скорости замедляет без изменения высоты тона.
5. **Save no guitar** или **Save no bass** скачивает минусовку в MP3.
6. **Clear saved songs** удаляет все загруженные песни и созданные файлы.

Песни и модель разделения хранятся в `%LOCALAPPDATA%\GuitarAndBassAISmartRemover`. Деинсталлятор останавливает приложение и удаляет эту папку вместе с сохранёнными песнями.

### Сборка из исходников

Нужны Windows 10 или 11 x64, Python 3.12 x64 и [Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Установщик появится в `dist\GuitarAndBassAISmartRemoverSetup.exe`. Для разработки запустите `python server.py` и откройте http://127.0.0.1:8765. Видеокарта CUDA не обязательна, но с ней разделение работает намного быстрее.
