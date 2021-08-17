while true; do
	echo -n "$(curl localhost:80/last_meme) $(date)"
	sleep 1
done
