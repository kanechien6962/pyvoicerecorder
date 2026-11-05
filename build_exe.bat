@echo off
REM python -m pip install -U pyinstaller pyinstaller-hooks-contrib sounddevice soundfile pydub numpy PyQt6 audioop-lts
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --icon "rec_256x256.ico" ^
  --collect-all sounddevice ^
  --collect-all soundfile ^
  --hidden-import=audioop_lts ^
  --name "rec" ^
  recorder_m4a.py
echo Done! Check dist\rec.exe
pause