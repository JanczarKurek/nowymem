#!/usr/bin/fish
cd /home/janczarknurek/not_work/www/nowymem/
rm meme_symlink
ln -s (pwd)/nohorny.jpg (pwd)/meme_symlink
feh meme_symlink &
set FEH_PID (jobs -lp)
source /home/janczarknurek/.venv/nowymem/bin/activate.fish
python nowymem.py --port=8080 $HOME --feh-pid "$FEH_PID" --feh-pic-path meme_symlink
