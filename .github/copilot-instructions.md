# Angolla Music Player - AI Aracı Talimatları

## Proje Özeti
**Angolla**, Clementine tarafından ilham alınan PyQt5 tabanlı tek dosyalı bir müzik oynatıcısıdır. Çevrimdışı çalışan masaüstü uygulaması (~3000+ satır) entegre ses görselleştirme (11 mod), gelişmiş ekolayzır ve kütüphane yönetimi ile beraber gelir. Türkçe arayüz ve gerçek FFT spektrumu analizi içerir.

## Mimari Yapı

### Temel Sınıf Hiyerarşisi
```
LibraryManager (SQLite DB yönetimi)
  ↓
[Görselleştirme Bileşenleri]
  - AnimatedVisualizationWidget (11 görselleştirme modu)
  - VisualizationWindow (tam ekran görselleştirme)
  - InfoDisplayWidget (albüm kapağı + metadata)
  - EqualizerWidget (10 bant EQ)
  - LibraryTableWidget (sıralanabilir parça tablosu)
  - PlaylistListWidget (sürükle-bırak)
  ↓
AngollaPlayer (QMainWindow)
  ↓
main() → QApplication
```

## Ses Veri Akışı (KRITIK - Görselleştirme için)

```
QMediaPlayer (oynatma)
  ↓
QAudioProbe.audioBufferProbed (ses buffer)
  ↓
process_audio_buffer() (FFT analizi)
  - NumPy ile 96-band frekans spektrumu
  - Şiddet hesaplaması (bass-oriented)
  ↓
send_visual_data(intensity, band_vals)
  - İki widget'e:
    • vis_widget_main_window (100px alt bar)
    • VisualizationWindow (tam ekran)
  ↓
update_sound_data() (Yumuşatma)
  - Per-band attack/release (frekansa göre)
  - Cap heights (Clementine tarzı tepeler)
  ↓
paintEvent() → Seçili modu render et
```

**Kod Konumları**:
- FFT: `process_audio_buffer()` (2420-2550)
- Veri gönderimi: `send_visual_data()` (2552-2560)
- Yumuşatma: `update_sound_data()` (1130-1220)

## Görselleştirme Modları (11 Toplam)

Tüm modlar `AnimatedVisualizationWidget`'da `_draw_*_mode()` olarak uygulanır:

1. **Çizgiler** - Parçacık sistemi, hız-temelli renk
2. **Daireler** - Merkez pulsating, spektrum noktaları + halo
3. **Spektrum Çubukları** - Tam ekran HSV çubuklar, parlama seviyeleri
4. **Enerji Halkaları** - Konsantrik halkalar + pulse noktaları
5. **Dalga Formu** - Sinüsoidal animasyon FFT üzerinde
6. **Pulsar** - Gradient ışınlar merkezden, açılı
7. **Spiral** - Logaritmik spiral + iz çizgileri
8. **Volcano** - Turuncu-sarı parçacıklar, glow efekti
9. **Işın Çakışması** - Sarı→turuncu→kırmızı kalın ışınlar
10. **Çift Spektrum** - Yukarı/aşağı simetrik spektrum
11. **Radyal Grid** - Izgara + radyal çubuklar

**Bar Rendering Giriş Noktası**: `_draw_status_bars()` (1600-1730)

## Yapılandırma & Depolama

```python
SETTINGS_KEY = "AngollaPlayer/Settings"

save_config() → pickle.dumps(config_data):
  - volume, shuffle_mode, repeat_mode
  - theme, show_album_art, vis_mode
  - eq_gains (10 float), bar_color, bar_style
  - crossfade_duration

save_playlist() → PLAYLIST_FILE:
  - file paths + current_index
  - Load time: path validation (missing remove)
```

## Temel İş Akışları

### Parçaları Ekleme
```python
_add_files_to_playlist(paths) → _add_media() OR _add_folder()
  - Doğrulama: .mp3, .flac, .ogg, .m4a, .wav
  - Mutagen metaveri çıkarma
  - QMediaContent + QListWidgetItem
  - Playlist & playlistWidget senkronize
```

### Kütüphane Taraması
```python
scan_library() → _add_folder(add_to_library=True)
  → LibraryManager.add_track()
    → SQLite INSERT OR REPLACE
  → refresh_library_view()
```

### Metaveri Çıkarma
```python
_get_tags_from_file_with_duration(path):
  - Primary: Mutagen (ID3/MP4)
  - Fallback: filename
  - Dönüş: (title, artist, album, duration_ms)
```

## Proje-Spesifik Kurallar

### Türkçe Adlandırma
- UI yazıları Türkçe
- Kod yorumları Türkçe
- Örnek: "Çizgiler" = Lines mode

### NumPy İsteğe Bağlı
```python
try:
    import numpy as np
except:
    np = None  # Fallback işlevi

# Bütün kod: if np is None checks
```

### Attack/Release Yumuşatması (KRİTİK)
`update_sound_data()` içinde (1130-1220):
```python
attack = 0.40 - 0.15 * frac      # ~0.40 (bass) → ~0.25 (treble)
release = 0.02 + 0.08 * frac     # ~0.02 (bass) → ~0.10 (treble)

# Yeni > eski: attack (hızlı çıkış bas)
# Yeni < eski: release (yavaş düşüş)
```
**DEĞİŞTİRMEYİN** - Animasyon hissi buna bağlı!

### Tema Sistemi
```python
self.themes = {
    "AURA Mavi": ("#40C4FF", "#FFFFFF", "#2A2A2A"),
    # (primary_hex, text, background)
}

set_theme(name) → QApplication.setStyleSheet()
```

### Bar Renk Modları
```python
bar_color_mode = "NORMAL"    # Tek renk
bar_color_mode = "RGB"       # HSV spektrum (0-360°)
bar_color_mode = "GRADYAN"   # Önceden tanımlanmış (mavi→kırmızı)
```

## Yaygın Tuzaklar

1. **Monolitik Tek Dosya**: Tüm sınıflar main.py'de. Refactoring yapma.

2. **Görselleştirme Widget İkiliği**:
   - `vis_widget_main_window` (100px)
   - `VisualizationWindow.visualizationWidget` (tam ekran)
   - `update_sound_data()` ile senkronize tutulmalı

3. **NumPy Bağımlılığı**: Kod yolu NumPy'ye bağlı. `if np is None` dallarını kontrol et.

4. **QAudioProbe Başarısızlığı**: 
   - Probe başarısız → `process_audio_buffer()` tetiklenmez
   - `_fallback_visual_update()` bar animasyonunu tutar
   - `self.probe_working` durumunu kontrol et

5. **Veritabanı Tutarlılığı**: Schema değişikliği = göç mantığı `_setup_db()`'ye

6. **Sürükle-Bırak Karmaşıklığı**:
   - file_tree: DragOnly
   - playlistWidget: InternalMove + DropOnly
   - Widget'ler arası: MIME doğrulaması gerekli

## Test Kontrol Listesi

- [ ] Oynat/duraklat + bar görselleştirme duyarlı
- [ ] 11 görselleştirme modu çalışıyor
- [ ] Sürükle-bırak dosyalar eklenebiliyor
- [ ] Tema değişimi → tüm widget'lar güncelleniyor
- [ ] EQ kaydırıcı → config kaydediliyor
- [ ] Kapat/yeniden aç → playlist+ayarlar kalıcı
- [ ] Kütüphane taraması → DB dolduruldu
- [ ] NumPy olmadan çalışıyor (fallback)

**Çalıştırma**:
```bash
python3 main.py
# Gerekli: PyQt5, mutagen (isteğe bağlı), numpy (isteğe bağlı)
pip install PyQt5 mutagen numpy
```

## Dosya Bölümleme

- **Satırlar 1-250**: İmportlar + sabitleri + LibraryManager
- **Satırlar 250-600**: LibraryTableWidget + EqualizerWidget
- **Satırlar 600-900**: InfoDisplayWidget + SeekSlider
- **Satırlar 900-1100**: PlaylistListWidget
- **Satırlar 1100-2100**: AnimatedVisualizationWidget (11 mod)
- **Satırlar 2100-2200**: VisualizationWindow
- **Satırlar 2200-2400**: AngollaPlayer.__init__ + layout
- **Satırlar 2400-2600**: Audio işleme (FFT, spektrum)
- **Satırlar 2600-2750**: Config/playlist depolama
- **Satırlar 2750-2850**: PreferencesDialog + main()

## Yeni Görselleştirme Modları (v2.0)

### 9. Işın Çakışması (Beam Collision)
- Gradyan ışınlar merkezden dışarı
- Sarı-turuncu-kırmızı renk geçişi
- Kalınlık merkezden sonuna doğru azalır
- **Kod**: `_draw_beam_collision_mode()` (~1380)

### 10. Çift Spektrum (Dual Spectrum)
- Yukarı/aşağı simetrik spektrum çubukları
- Orta çizgide kavşak
- HSV renk ters çevirme (hue+180)
- **Kod**: `_draw_dual_spectrum_mode()` (~1420)

### 11. Radyal Grid (Radial Grid)
- Izgara çizgileri merkez etrafında
- Radyal çubuklar içten dışa
- Spektrum renk hue döngüsü
- **Kod**: `_draw_radial_grid_mode()` (~1450)
