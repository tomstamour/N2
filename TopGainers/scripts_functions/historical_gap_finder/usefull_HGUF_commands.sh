# Usefull commands to run the HGUF script
python3 ./Historical_Gap_Up_Finder.py \
  --time-window "01-11-2025" "20-12-2025" \
  --universe-file test_50_symbols.txt \
  --minimum-gap-percentage 30 --max-open-price 10.0 --max-float 20000000 \
  --candles-size 10 --client-id 700 \
  --output-dir hguf-tables \
  --log logs/HGaUF-log-5.txt

python3 ./Historical_Gap_Up_Finder.py \
  --time-window "01-03-2025" "19-12-2025" \
  --universe-file stock_universe_12k.txt \
  --minimum-gap-percentage 30 --max-open-price 50.0 --max-float 200000000 \
  --GUSday-candles-size 10 \
  --nonGUSday-candles-size 240 \
  --client-id 700 \
  --output-dir hguf-tables \
  --log logs/HGUF-log-9.txt \
  --fetch-time-pre-gap 24.0 \
  --fetch-time-post-gap 48.0

  python3 ./Historical_Gap_Up_Finder.py \
    --time-window "01-01-2023" "29-12-2023" \
    --universe-file stocks_gus_mart.txt \
    --minimum-gap-percentage 47 \
    --max-open-price 30.0 \
    --max-float 200000000 \
    --GUSday-candles-size 10 \
    --nonGUSday-candles-size 240 \
    --client-id 700 \
    --output-dir hguf-tables \
    --log logs/HGUF-log-10.txt \
    --fetch-time-pre-gap 24.0 \
    --fetch-time-post-gap 72.0 \
    --no-cache
