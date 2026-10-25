# Media Library

Lokal medieserver för Windows med eget desktopfönster: skanna dina egna mappar
med filmer och serier, få affischer och metadata från TMDB, spela upp i appen
eller på en Chromecast, sök torrents via Prowlarr/Jackett och skicka dem till
qBittorrent – allt på din egen dator.

Till appen hör också [**DeckLink**](../DeckLink), telefonappen som gör mobilen
till en Bluetooth-handkontroll (styrplatta, D-pad, A/B och spelarknappar) för
Media Library. DeckLink behöver inget program på datorn – Windows ser telefonen
som ett vanligt tangentbord och en mus.

> Använd bara torrentkällor och material som du har rätt att komma åt. Media
> Library innehåller ingen egen torrentsajt eller torrentkatalog.

---

## Innehåll

1. [Hur appen fungerar](#hur-appen-fungerar)
2. [Installera och starta](#installera-och-starta)
3. [Första starten](#första-starten)
4. [Gränssnittet](#gränssnittet)
5. [Titelsidan](#titelsidan)
6. [Spelaren](#spelaren)
7. [Undertexter](#undertexter)
8. [Nedladdningar](#nedladdningar)
9. [Inställningar](#inställningar)
10. [Styra med handkontroll eller DeckLink](#styra-med-handkontroll-eller-decklink)
11. [Desktopfönstret](#desktopfönstret)
12. [Data, säkerhet och backup](#data-säkerhet-och-backup)
13. [Bygga en egen .exe](#bygga-en-egen-exe)
14. [Androidklienten](#androidklienten)
15. [Felsökning](#felsökning)
16. [Projektstruktur](#projektstruktur)

---

## Hur appen fungerar

Media Library är en **lokal server** (FastAPI) som startar på port `8420` och
serverar ett webbgränssnitt. Desktopappen är ett tunt skal (PySide6/QtWebEngine)
runt samma gränssnitt, så webbversionen och desktopversionen är identiska.

- Datorn är servern och måste vara igång när du använder appen.
- Allt körs i ditt lokala nätverk. Inget skickas till någon molntjänst förutom
  TMDB (metadata/affischer), OpenSubtitles (valfri undertextsökning) och de
  indexerare du själv kopplar in.
- Androidklienten och DeckLink ansluter till datorservern; de läser inte disk
  direkt.
- Skanning, metadata och tittarhistorik sparas i en lokal SQLite-databas.

---

## Installera och starta

### Krav

- Windows 10/11
- **Python 3.11 eller senare** i PATH (endast när du kör från källkoden)
- `ffmpeg`/`ffprobe` i `tools/ffmpeg/` för transkodning och cast

### Starta desktopappen från källkoden

1. Dubbelklicka på [`run.bat`](run.bat).
2. Första gången skapas `.venv` och alla Python-paket installeras – det tar en
   stund och kräver internet.
3. Appen öppnas i ett eget, ramlöst fönster.

Kör du redan ett bygge finns [`dist/MediaLibraryDesktop.exe`](dist) och startas
med samma `run.bat`.

Vill du hellre köra som webbsida: starta `run.bat` och öppna
`http://localhost:8420` i Chrome eller Edge. Webbläsarversionen har samma
funktioner; desktopfönstret ger dessutom handkontrollsstöd via skalet.

### Stänga

Stäng fönstret (eller terminalfönstret om du kör webbversionen). Servern stängs
då och porten frigörs. En ny start återanvänder en server som redan kör, så du
kan inte råka starta två kopior.

---

## Första starten

1. Öppna **Inställningar → Bibliotek**.
2. Lägg in en sökväg per rad till dina mediamappar, till exempel:

   ```text
   C:\Users\Robert\Videos
   D:\Media\Filmer
   D:\Media\TV
   ```

3. Tryck **Spara** och sedan **Skanna om**.
4. Öppna **Inställningar → TMDB**, klistra in din API-nyckel och tryck
   **Testa**. Kör **Matcha om metadata** om biblioteket redan skannats.

Skanningen ändrar aldrig dina originalfiler. Vanliga videoändelser hittas
automatiskt; mappar som `Extras`, `Sample`, `Trailers` och `Subs` hoppas över,
liksom filer under ungefär 20 MB. För serier hjälper tydlig namngivning:

```text
Videos\Serienamn\Season 01\Serienamn - S01E02 - Avsnittstitel.mkv
```

### TMDB-nyckel

1. Skapa ett konto på [themoviedb.org](https://www.themoviedb.org/).
2. Kontoinställningar → **API** → skapa en utvecklarnyckel.
3. Kopiera **API Key (v3 auth)** eller en v4 Read Access Token.
4. Klistra in i **Inställningar → TMDB** och tryck **Testa**.

Nyckeln sparas bara lokalt i `data/settings.json` och skickas inte till
webbläsaren. Utan TMDB fungerar uppspelning, men affischer och synopsis blir
tomma.

---

## Gränssnittet

Överst sitter en topbar med sökfält, meny och fönsterknappar (i desktopappen).

| Menyval | Innehåll |
| --- | --- |
| **Hem** | Utvalda rader: Fortsätt titta, nyligen tillagda, filmer och serier. |
| **Filmer** | Alla identifierade filmer, med sorteringsval. |
| **Serier** | Serier grupperade per show med säsonger och avsnitt. |
| **Nedladdningar** | Sök & lägg till, Aktiva och Importerat. |
| **Inställningar** | All konfiguration, statistik och diagnostik. |

- **Sökfältet** söker i ditt redan indexerade bibliotek – inte efter nya
  torrents.
- **Bakåtknappen** (längst upp till vänster på titelsidor och i listor) tar dig
  tillbaka till exakt den vy och det läge du kom ifrån, inklusive scrollposition
  och markering.
- Korten på Hem kan ha en liten **×** för att ta bort titeln från **Fortsätt
  titta**; filen ligger kvar på disken.
- Tangentbord och handkontroll flyttar en tydlig fokusring mellan korten, så du
  kan navigera helt utan mus.

---

## Titelsidan

Klicka på en film eller ett avsnitt för titelsidan:

| Knapp | Vad den gör |
| --- | --- |
| **Spela** | Startar spelaren och hoppar till senaste position. |
| **Casta** | Skickar uppspelningen till en Chromecast i nätverket. |
| **Byt matchning** | Söker fram rätt TMDB-post när filnamnet gav fel träff. |
| **Markera som sedd** | Sätter/rensar visningsstatus för filmen eller avsnittet. |
| **Öppna mappen** | Öppnar filmappen i Utforskaren med filen markerad. |
| **Radera från disk** | Raderar själva videofilen efter bekräftelse. |
| **Ta bort från Fortsätt titta** | Glömmer tittarpositionen, filen ligger kvar. |

Serier visar säsongsväljare och avsnittslista. Klicka på ett avsnitt för att
spela upp det direkt, eller på avsnittets titel för dess egen sida.

Filinformation längst ned visar container, videocodec, upplösning, ljudspår,
undertextspår och filstorlek – användbart när något inte vill spelas upp.

---

## Spelaren

Spelaren startar i fönsterläge. Under bilden ligger kontrollraden, så den
täcker aldrig videon. I fullskärm läggs kontrollerna ovanpå bilden och tonas
bort automatiskt efter några sekunder utan musrörelse – även muspekaren göms.

### Kontroller

| Åtgärd | Mus / tangent |
| --- | --- |
| Spela/pausa | Klick i bilden, `K` eller `Space` |
| Hoppa 10 sekunder | `←` / `→` |
| Hoppa 30 sekunder | `J` / `L` |
| Fullskärm | `F` eller dubbelklick i bilden |
| Lämna fullskärm | `Esc` |
| Stäng spelaren | `Esc` (när du inte är i fullskärm) |
| Spola till valfri tid | Klick eller dra i tidslinjen |
| Undertexter | **CC Undertexter** i kontrollraden |
| Casta | **Casta** i kontrollraden |

Kontrollraden visar också tid, total längd och vilket uppspelningsläge som
används: *Direktuppspelning* (originalfilen), *HLS* eller *MP4 · H.264* när
servern transkodar för webbläsaren eller Chromecast.

Spelaren sparar positionen löpande, så titeln dyker upp i **Fortsätt titta** och
fortsätter där du slutade.

---

## Undertexter

**CC Undertexter** i spelaren öppnar undertextmenyn med tre vägar:

1. **Befintliga spår** – undertexter som ligger bredvid videofilen, eller
   inbäddade spår i filen. Appen hittar filer som `Film.srt`,
   `Film.sv.srt`, `Film.en.srt` och matchar mot videons basnamn, även när
   undertexten inte har exakt samma namn som videon.
2. **Sök online** (OpenSubtitles) – kräver API-nyckel, se
   **Inställningar → Undertexter (OpenSubtitles)**. Sökningen använder titel,
   TMDB/IMDb-ID, säsong/avsnitt och filhash för att hitta rätt. Ladda ned en
   träff och den sparas bredvid videon och slås på direkt.
3. **Generera** – kör den lokala undertextgeneratorn (Whisper) på datorn. Välj
   **svenska** eller **engelska**; appen frågar i rutan och visar en
   statusrad med procent, förfluten tid och uppskattad återstående tid.
   Resultatet sparas som `Filnamn.sv.srt` respektive `Filnamn.en.srt` bredvid
   videon.

Genereringen använder modellen *small* och går på grafikkortet när CUDA finns
tillgängligt (inbyggt i `Undertextgenerator.exe`), annars på processorn – då
tar det betydligt längre tid. Genomsnittstiden per videominut visas i
**Inställningar → Generera undertexter** tillsammans med antal körningar, så du
kan bedöma hur lång tid en film tar.

Undertexterna ritas av appen själv (inte av webbläsarens `<track>`), vilket gör
att de hamnar ovanför kontrollraden i fönsterläge och följer med när raden göms
i fullskärm.

---

## Nedladdningar

Tre flikar:

### Sök & lägg till

1. Skriv en film- eller serietitel och tryck **Sök**.
2. Resultatlistan visar titel, indexerare, storlek och seeders.
3. Tryck **Ladda ner** för att skicka magneten till qBittorrent.
4. Du kan också klistra in en magnetlänk eller välja en `.torrent`-fil.

Media Library söker via **Prowlarr** eller **Jackett** – du behöver minst en av
dem, se [Inställningar](#inställningar). Appen har ingen egen tracker.

### Aktiva

Visar qBittorrent-jobben med status, procent, hastighet, peers/seeds och
återstående tid. Listan uppdateras automatiskt.

| Åtgärd | Effekt |
| --- | --- |
| Pausa / Återuppta | Styr jobbet i qBittorrent. |
| Filer och prioritet | Sätter prioritet per fil (Hoppa över/Låg/Normal/Hög). |
| Importera till biblioteket | Importerar en färdig torrent manuellt. |
| Ta bort torrent | Tar bort jobbet, behåller filerna. |
| Ta bort + filer | Tar bort jobb och filer efter bekräftelse. |

### Importerat

Lista över filer som importören nyligen lagt i biblioteket.

### Automatisk import

I **Inställningar → Import & nedladdade filer** ställer du in auto-import,
målmappar för film respektive serier och importläge:

- **Hårdlänk** – ingen extra kopia, torrenten kan fortsätta seeda. Kräver att
  nedladdningsmapp och bibliotek ligger på samma volym.
- **Kopiera** – fungerar över volymer men kräver dubbelt utrymme.
- **Flytta** – sparar utrymme, men torrenten kan inte seeda vidare.

---

## Inställningar

| Sektion | Innehåll |
| --- | --- |
| **Kom igång** | Kort checklista för bibliotek, TMDB och nedladdningar. |
| **Bibliotek** | Rotmappar som skannas, **Skanna om** och **Matcha om metadata**. |
| **TMDB** | API-nyckel/token och **Testa**. |
| **qBittorrent** | Web UI-adress (`http://localhost:8080`), användare, lösenord, **Testa anslutning**. |
| **Indexerare** | Prowlarr/Jackett-adress och API-nycklar, **Testa**. |
| **Undertexter (OpenSubtitles)** | API-nyckel för online-sökning. |
| **Generera undertexter** | Statistik för lokal generering (tid per videominut, antal körningar). |
| **Import & nedladdade filer** | Auto-import, importläge, målmappar, nedladdningsmapp. |
| **Chromecast & transcoding** | Maxhöjd, bitrate och om transkodning ska tvingas. |
| **Uppspelningslogg** | Diagnostik för de senaste uppspelningsförsöken – **Kopiera logg** när något inte spelar. |

---

## Styra med handkontroll eller DeckLink

Gränssnittet har ett komplett tangentbords-/handkontrollsläge:

| Knapp | Effekt |
| --- | --- |
| Piltangenter / D-pad | Flyttar fokusringen mellan kort, avsnitt, knappar och fält. |
| `Enter` / **A** | Väljer det som är markerat. |
| `Esc` / **B** | Går tillbaka ett steg (stänger spelare eller dialog). |

Fokuseringen fungerar också i textfält: ringen landar på sökfältet och i
inställningsfälten, så du kan navigera hela appen utan mus. Handkontrollen läses
av desktopskalet (Qt), och DeckLink-telefonen skickar riktiga tangenttryck som
Windows förmedlar – appen behöver alltså inte veta vilken enhet som styr.

**DeckLink** är telefonappen som är byggd för Media Library: styrplatta med
klick och tvåfingersscroll, D-pad, A/B och en spelarpanel med −30 s (J),
−10 s (←), paus (K), +10 s (→), +30 s (L), helskärm (F) och stäng (Esc).
Se [DeckLink/README.md](../DeckLink/README.md) för parkoppling och användning.

---

## Desktopfönstret

- Eget, ramlöst fönster utan Windows titelrad – knapparna minimera, maximera
  och stäng ligger i appens topbar.
- Fönstret kan flyttas genom att dra i topbaren och storleksändras genom att dra
  i kanterna.
- Endast en instans: startar du appen igen återanvänds den server som redan kör.
- Data hamnar i `%LOCALAPPDATA%\MediaLibrary\data` när du kör `.exe`, och i
  projektets `data/` när du kör från källkoden.

---

## Data, säkerhet och backup

```text
data/settings.json     inställningar och API-nycklar – dela inte
data/library.db        biblioteksindex och tittarhistorik
data/cache/            bild- och metadatacache
data/hls/              tillfälliga transkodningsfiler
data/playback.log      diagnostik för uppspelning
```

- `data/settings.json` innehåller TMDB-nyckel, OpenSubtitles-nyckel,
  indexerar-API-nycklar och qBittorrent-inloggning. Publicera eller dela den
  aldrig.
- Säkerhetskopiera `settings.json` och `library.db` om du vill behålla
  konfiguration och historik. Cache och transkodning kan återskapas.
- Servern lyssnar på nätverket så att telefon och Chromecast ska nå den. Kör
  den bara på ett betrott privat nätverk och exponera aldrig port `8420` eller
  qBittorrents Web UI mot internet utan en separat säkerhetslösning.

---

## Bygga en egen .exe

```powershell
cd MediaLibrary
.\.venv\Scripts\python.exe -m PyInstaller MediaLibraryDesktop.spec --noconfirm --distpath dist --workpath build
```

Resultatet blir `dist\MediaLibraryDesktop.exe`. Speccen bäddar in webbgränssnittet,
Python-paketen, `assets/` (appikonen) och `../SubtitleGenerator/dist/Undertextgenerator.exe`,
så bygg undertextgeneratorn först om du vill kunna generera undertexter i den
färdiga appen. Filen blir stor eftersom CUDA-biblioteken för Whisper ingår.

---

## Androidklienten

`android/` innehåller en native Android-klient (Kotlin) för biblioteket,
serierna, nedladdningarna och uppspelning via servern.

1. Öppna `MediaLibrary/android` i Android Studio.
2. Bygg och installera på telefonen.
3. Starta datorservern och se till att telefon och dator är på samma Wi-Fi.
4. Ange datorns LAN-adress och port i appen, till exempel `192.168.1.25:8420`
   (`http://`-prefix går också bra).

Den läser inte datorns disk direkt utan använder serverns API. Komplettera
gärna med **DeckLink** för att styra appen från telefonen.

---

## Felsökning

| Symptom | Kontrollera |
| --- | --- |
| `localhost:8420` öppnas inte | Serverns terminalfönster måste vara öppet. Kontrollera att port 8420 inte används av något annat. |
| Inga filmer/serier | Sökvägarna i **Inställningar → Bibliotek** och kör **Skanna om**. Filer under ca 20 MB ignoreras. |
| Inga affischer | TMDB-nyckeln, **Testa**, sedan **Matcha om metadata**. Fel matchning rättas med **Byt matchning**. |
| Fel film matchad | **Byt matchning** på titelsidan och sök fram rätt post. |
| Sökningen hittar inga torrents | Prowlarr/Jackett måste vara konfigurerade och deras egna indexerare fungera. |
| Resultat kan inte läggas till | qBittorrent Web UI, port, användarnamn/lösenord och **Testa anslutning**. |
| qBittorrent svarar 403 | Web UI:s IP-filter och inloggning; exponera inte porten mot internet. |
| Undertexten syns inte | Filen ska ligga bredvid videon och heta samma sak, t.ex. `Film.sv.srt`. `.ass`/`.ssa` kan hittas men stöds inte av webbläsarspelaren. |
| Undertextgenereringen tar lång tid | Körningen går på CPU om CUDA inte hittas. Kontrollera GPU-drivrutin, eller välj en kortare titel först. |
| Videon spelas inte upp | Öppna **Inställningar → Uppspelningslogg** och kopiera loggen – den visar codec, läge och felet. |
| Hårdlänk-import misslyckas | Nedladdning och bibliotek måste ligga på samma disk. Byt till kopiera eller flytta. |
| Cast startar men ingen bild | Låt transkodningen starta; kontrollera att `tools/ffmpeg/` finns och att brandväggen släpper igenom Python på privata nätverk. |
| Chromecast hittas inte | Samma nätverk, 2,4 GHz för första generationen, och att datorns LAN-adress går att nå från TV:n. |

---

## Projektstruktur

```text
MediaLibrary/
  run.bat                    startar servern/desktopappen
  desktop.py                 Qt-skalet (ramlöst fönster, handkontroll, fullskärm)
  app/                       FastAPI, skanner, metadata, qBittorrent, cast, undertexter
  web/                       webbgränssnitt (HTML/CSS/JS)
  assets/                    appikon (.png/.ico)
  tools/ffmpeg/              ffmpeg/ffprobe
  android/                   native Android-klient
  MediaLibraryDesktop.spec   PyInstaller-spec för .exe
  data/                      lokal data (skapas vid körning, versionshanteras inte)
```

### Tekniskt flöde

1. `desktop.py` (eller uvicorn direkt) startar FastAPI och monterar `web/` samt
   JSON-API:t.
2. Skannern går igenom biblioteksmapparna, tolkar film-/serienamn och sparar
   poster i SQLite.
3. TMDB-klienten matchar titlar och hämtar affischer/metadata; bilder cachas.
4. Webbgränssnittet hämtar allt via `/api/...`.
5. Torrentsökning går via Prowlarr/Jackett; valda resultat skickas till
   qBittorrent Web API.
6. Importören flyttar/länkar färdiga nedladdningar till biblioteket och skannar
   om.
7. Uppspelning sker direkt när formatet stöds, annars transkodar ffmpeg till HLS
   eller fragmenterad MP4 – för Chromecast anpassat efter mottagaren.
