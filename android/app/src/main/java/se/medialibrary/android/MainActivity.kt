package se.medialibrary.android

import android.app.AlertDialog
import android.content.Context
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

private const val BG = 0xff10110f.toInt()
private const val PANEL = 0xff1b1c18.toInt()
private const val SYSTEM_BAR = 0xff000000.toInt()
private const val YELLOW = 0xffffc857.toInt()
private const val MUTED = 0xffaaa89e.toInt()

@UnstableApi
class MainActivity : ComponentActivity() {
    private var base = ""
    private lateinit var root: LinearLayout
    private var player: ExoPlayer? = null
    private var currentItems = JSONArray()
    private var currentShow: JSONObject? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        base = getPreferences(Context.MODE_PRIVATE).getString("server", "") ?: ""
        window.statusBarColor = SYSTEM_BAR
        window.navigationBarColor = SYSTEM_BAR
        if (android.os.Build.VERSION.SDK_INT >= 29) window.isNavigationBarContrastEnforced = false
        showConnect()
    }

    private fun dp(n: Int) = (n * resources.displayMetrics.density).toInt()
    private fun request(path: String, method: String = "GET", body: JSONObject? = null): JSONObject {
        val conn = (URL(base.trimEnd('/') + path).openConnection() as HttpURLConnection)
        conn.connectTimeout = 7000; conn.readTimeout = 25000; conn.requestMethod = method
        if (body != null) { conn.doOutput = true; conn.setRequestProperty("Content-Type", "application/json"); conn.outputStream.use { it.write(body.toString().toByteArray()) } }
        val text = (if (conn.responseCode < 400) conn.inputStream else conn.errorStream).bufferedReader().use { it.readText() }
        if (conn.responseCode >= 400) throw IllegalStateException(text)
        return JSONObject(text)
    }

    private fun showConnect() {
        val box = LinearLayout(this).apply { orientation = 1; setPadding(dp(28), dp(32), dp(28), dp(24)); setBackgroundColor(BG) }
        box.addView(label("NEON / REEL", 30, YELLOW, true))
        box.addView(label("Ditt bibliotek. Din dator. Din TV.", 15, MUTED))
        val addr = EditText(this).apply { hint = "http://192.168.1.20:8420"; setSingleLine(); setText(base); setTextColor(Color.WHITE); setHintTextColor(MUTED); setPadding(dp(12), dp(12), dp(12), dp(12)); setBackgroundColor(PANEL) }
        box.addView(addr, LinearLayout.LayoutParams(-1, dp(54)).apply { topMargin = dp(28) })
        box.addView(button("ANSLUT TILL DATORN") {
            var address = addr.text.toString().trim().trimEnd('/')
            if (address.isNotEmpty() && !address.contains("://")) address = "http://$address"
            if (address.isBlank()) { toast("Ange datorns adress, t.ex. 192.168.1.25:8420"); return@button }
            val parsed = Uri.parse(address)
            if (parsed.scheme !in listOf("http", "https") || parsed.host.isNullOrBlank()) { toast("Ogiltig adress. Ange t.ex. http://192.168.1.25:8420"); return@button }
            base = address
            getPreferences(0).edit().putString("server", base).apply()
            loadHome()
        }, LinearLayout.LayoutParams(-1, dp(52)).apply { topMargin = dp(12) })
        box.addView(label("Starta MediaLibrary på datorn och anslut till samma Wi‑Fi.", 13, MUTED))
        setContentView(box)
    }

    private fun scaffold(title: String, active: String = "Hem") {
        window.statusBarColor = SYSTEM_BAR
        window.navigationBarColor = SYSTEM_BAR
        root = LinearLayout(this).apply { orientation = 1; setBackgroundColor(BG) }
        val top = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(dp(18), dp(14), dp(18), dp(10)) }
        top.addView(label("NEON / REEL", 21, YELLOW, true), LinearLayout.LayoutParams(0, -2, 1f))
        top.addView(button("⌕") { showSearch() }, LinearLayout.LayoutParams(dp(48), dp(42)))
        root.addView(top)
        root.addView(label(title, 27, Color.WHITE, true).apply { setPadding(dp(20), dp(8), dp(16), dp(8)) })
        val scroll = ScrollView(this); val content = LinearLayout(this).apply { orientation = 1; setPadding(dp(16), dp(8), dp(16), dp(22)) }
        scroll.addView(content); root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        val nav = LinearLayout(this).apply { setPadding(dp(8), dp(8), dp(8), dp(8)); setBackgroundColor(PANEL) }
        listOf("⌂  Hem", "▤  Filmer", "▣  Serier", "⇩  Torrents").forEach { item -> nav.addView(button(item) { when { item.contains("Hem") -> loadHome(); item.contains("Filmer") -> loadMovies(); item.contains("Serier") -> loadShows(); else -> showDownloads() } }, LinearLayout.LayoutParams(0, dp(48), 1f)) }
        root.addView(nav); setContentView(root)
    }

    private fun content() = (root.getChildAt(2) as ScrollView).getChildAt(0) as LinearLayout
    private fun loadHome(): Unit {
        fetch("/api/home") { data: JSONObject ->
            scaffold("Upptäck")
            val c: LinearLayout = content()
            val rows: JSONArray = data.optJSONArray("rows") ?: JSONArray()
            for (i in 0 until rows.length()) {
                val row: JSONObject = rows.optJSONObject(i) ?: continue
                val title: String = row.optString("title")
                val items: JSONArray = row.optJSONArray("items") ?: continue
                addShelf(c, title, items)
            }
            if (rows.length() == 0) c.addView(label("Biblioteket laddas eller är tomt. Skanna om i datorns MediaLibrary.", 16, MUTED))
            c.addView(button("⟳  Skanna biblioteket") { post("/api/scan", JSONObject()) { _: JSONObject -> loadHome() } })
        }
    }

    private fun loadMovies(): Unit = fetch("/api/movies") { data: JSONObject -> scaffold("Filmer"); val arr: JSONArray = data.optJSONArray("items") ?: JSONArray(); currentItems = arr; addShelf(content(), "Ditt bibliotek", arr, true) }
    private fun loadShows(): Unit = fetch("/api/shows") { data: JSONObject -> scaffold("Serier"); val arr: JSONArray = data.optJSONArray("items") ?: JSONArray(); currentItems = arr; addShelf(content(), "Serier i biblioteket", arr, false, true) }

    private fun addShelf(parent: LinearLayout, title: String, arr: JSONArray, movies: Boolean = false, shows: Boolean = false) {
        parent.addView(label(title, 18, YELLOW, true).apply { setPadding(0, dp(12), 0, dp(8)) })
        if (arr.length() == 0) { parent.addView(label("Inget innehåll ännu", 14, MUTED)); return }
        val horizontal = HorizontalScrollView(this); val line = LinearLayout(this)
        for (i in 0 until arr.length()) {
            val item = arr.optJSONObject(i) ?: continue
            val card = LinearLayout(this).apply { orientation = 1; setPadding(dp(5), dp(5), dp(5), dp(9)); setBackgroundColor(PANEL) }
            val poster = ImageView(this).apply { scaleType = ImageView.ScaleType.CENTER_CROP; setBackgroundColor(0xff292a24.toInt()) }
            val posterUrl = item.optString("poster", item.optString("poster_url", ""))
            if (posterUrl.startsWith("/")) loadImage("/api/img?p=" + URLEncoder.encode(posterUrl, "UTF-8") + "&size=w300", poster) else if (posterUrl.startsWith("http")) loadImage(posterUrl, poster)
            card.addView(poster, LinearLayout.LayoutParams(dp(142), dp(190)))
            card.addView(label(item.optString("title", item.optString("name", "Namn saknas")), 13, Color.WHITE, true).apply { maxLines = 2; setPadding(dp(3), dp(7), dp(3), 0) })
            card.setOnClickListener { when { shows -> openShow(item); item.optString("kind") == "episode" -> playItem(item); else -> showDetail(item) } }
            line.addView(card, LinearLayout.LayoutParams(dp(152), ViewGroup.LayoutParams.WRAP_CONTENT).apply { rightMargin = dp(9) })
        }
        horizontal.addView(line); parent.addView(horizontal)
    }

    private fun openShow(show: JSONObject) { val id = show.optInt("id"); fetch("/api/show/$id") { data -> currentShow = data; val seasonsArr = data.optJSONArray("seasons") ?: JSONArray(); scaffold(data.optJSONObject("show")?.optString("title", show.optString("title")) ?: show.optString("title")); val c = content(); val seasons = linkedMapOf<Int, MutableList<JSONObject>>()
        for (i in 0 until seasonsArr.length()) { val seasonObj = seasonsArr.optJSONObject(i) ?: continue; val episodes = seasonObj.optJSONArray("episodes") ?: JSONArray(); val seasonNum = seasonObj.optInt("season"); for (j in 0 until episodes.length()) { val e = episodes.optJSONObject(j) ?: continue; seasons.getOrPut(seasonNum) { mutableListOf() }.add(e) } }
        seasons.forEach { (season, episodes) -> c.addView(label("Säsong $season", 19, YELLOW, true)); episodes.forEach { ep -> val row = button("▶   S${ep.optInt("season").toString().padStart(2,'0')}E${ep.optInt("episode").toString().padStart(2,'0')}   ${ep.optString("title")}") { playItem(ep) }; c.addView(row) } }
    } }

    private fun showDetail(item: JSONObject) { val title = item.optString("title", item.optString("name")); AlertDialog.Builder(this).setTitle(title).setMessage(item.optString("overview", item.optString("description", "Ingen beskrivning tillgänglig.")) + "\n\n" + item.optString("year", "") + "   ★ " + item.optString("rating", "—")).setPositiveButton("Spela") { _, _ -> playItem(item) }.setNeutralButton("Casta") { _, _ -> cast(item) }.setNegativeButton("Stäng", null).show() }

    private fun playItem(item: JSONObject) {
        val id = item.optInt("id"); if (id <= 0) { toast("Saknar fil-ID"); return }
        fetch("/api/subtitles/$id") { data -> val options = mutableListOf("Ingen undertext"); val subs = data.optJSONArray("subtitles") ?: JSONArray(); for (i in 0 until subs.length()) { val s = subs.optJSONObject(i) ?: continue; options.add(s.optString("name", "Undertext ${i+1}")) }
            AlertDialog.Builder(this).setTitle("Välj undertext").setItems(options.toTypedArray()) { _, which -> startPlayback(id, if (which > 0) subs.optJSONObject(which-1) else null) }.setNegativeButton("Avbryt", null).show()
        }
    }

    private fun startPlayback(id: Int, subtitle: JSONObject?) {
        fetch("/api/play/$id") { data ->
            val url = data.optString("url").let { if (it.startsWith("http")) it else base + it }
            val view = PlayerView(this).apply { useController = true; setBackgroundColor(Color.BLACK) }
            val player = ExoPlayer.Builder(this).build(); this.player?.release(); this.player = player; view.player = player
            val media = MediaItem.Builder().setUri(url).apply { if (data.optString("mode") == "hls") setMimeType(MimeTypes.APPLICATION_M3U8) }.build()
            player.setMediaItem(media); player.prepare(); player.playWhenReady = true
            if (subtitle != null) { val subUrl = subtitle.optString("url"); if (subUrl.isNotBlank()) { val sub = MediaItem.SubtitleConfiguration.Builder(Uri.parse(if (subUrl.startsWith("http")) subUrl else base+subUrl)).setMimeType(MimeTypes.APPLICATION_SUBRIP).setLanguage("sv").setSelectionFlags(androidx.media3.common.C.SELECTION_FLAG_DEFAULT).build(); player.setMediaItem(MediaItem.Builder().setUri(url).setSubtitleConfigurations(listOf(sub)).build()); player.prepare(); player.playWhenReady = true } }
            AlertDialog.Builder(this).setView(view).setOnDismissListener { player.release(); this.player = null }.show()
        }
    }

    private fun cast(item: JSONObject) { fetch("/api/cast/devices?force=1") { data -> val devices = data.optJSONArray("devices") ?: JSONArray(); val labels = (0 until devices.length()).map { devices.optJSONObject(it)?.optString("name", "Chromecast") ?: "Chromecast" }.toTypedArray(); if (labels.isEmpty()) toast("Ingen Chromecast hittades"); else AlertDialog.Builder(this).setTitle("Välj Chromecast").setItems(labels) { _, which -> val dev = devices.optJSONObject(which); post("/api/cast/play", JSONObject().put("item_id", item.optInt("id")).put("uuid", dev?.optString("uuid"))) { toast("Skickar till TV:n") } } .show() } }

    private fun showDownloads() { fetch("/api/downloads") { data -> scaffold("Torrents"); val c = content(); c.addView(label(data.optString("error", "qBittorrent-status"), 14, MUTED)); val torrents = data.optJSONArray("torrents") ?: JSONArray(); for (i in 0 until torrents.length()) { val t = torrents.optJSONObject(i) ?: continue; c.addView(label("${t.optString("name")}  ·  ${(t.optDouble("progress")*100).toInt()}%", 14, Color.WHITE)); c.addView(button("Importera till biblioteket") { post("/api/downloads/${t.optString("hash")}/import", JSONObject()) { loadHome() } }) }; c.addView(button("Sök torrent") { torrentSearch() }); c.addView(button("Lägg till magnetlänk") { val input=EditText(this); AlertDialog.Builder(this).setTitle("Magnetlänk").setView(input).setPositiveButton("Lägg till") { _, _ -> post("/api/downloads/add", JSONObject().put("magnet", input.text.toString())) { showDownloads() } }.setNegativeButton("Avbryt", null).show() }) } }

    private fun torrentSearch() {
        val input = EditText(this).apply { hint = "Film eller serie"; setSingleLine() }
        AlertDialog.Builder(this).setTitle("Sök via Prowlarr / Jackett").setView(input)
            .setPositiveButton("Sök") { _, _ ->
                val query = input.text.toString().trim()
                if (query.isBlank()) { toast("Skriv en titel att söka efter"); return@setPositiveButton }
                fetch("/api/downloads/search?q=" + URLEncoder.encode(query, "UTF-8")) { data: JSONObject ->
                    val results: JSONArray = data.optJSONArray("results") ?: JSONArray()
                    if (results.length() == 0) {
                        val errors = data.optJSONArray("errors")
                        val detail = if (errors != null && errors.length() > 0) errors.optString(0) else "Inga träffar. Kontrollera att Prowlarr eller Jackett är konfigurerad i datorappen."
                        AlertDialog.Builder(this).setTitle("Inga torrentträffar").setMessage(detail).setPositiveButton("OK", null).show()
                    } else {
                        val names = (0 until results.length()).map { index -> results.optJSONObject(index)?.optString("title", "Resultat") ?: "Resultat" }.toTypedArray()
                        AlertDialog.Builder(this).setTitle("Sökresultat: $query").setItems(names) { _, index ->
                            val selected = results.optJSONObject(index) ?: return@setItems
                            post("/api/downloads/add", JSONObject().put("result", selected)) { _: JSONObject -> toast("Torrent skickad till qBittorrent"); showDownloads() }
                        }.setNegativeButton("Avbryt", null).show()
                    }
                }
            }.setNegativeButton("Avbryt", null).show()
    }
    private fun showSearch() { val input=EditText(this).apply { hint="Sök filmer och serier" }; AlertDialog.Builder(this).setTitle("Sök biblioteket").setView(input).setPositiveButton("Sök") { _, _ -> fetch("/api/search?q="+URLEncoder.encode(input.text.toString(),"UTF-8")) { data -> val movies=data.optJSONArray("movies")?:JSONArray(); val shows=data.optJSONArray("shows")?:JSONArray(); val items=JSONArray(); for(i in 0 until movies.length()) items.put(movies.getJSONObject(i)); for(i in 0 until shows.length()) items.put(shows.getJSONObject(i)); scaffold("Sökresultat"); addShelf(content(), input.text.toString(), items) } }.setNegativeButton("Avbryt",null).show() }

    private fun <T> fetch(path: String, success: (JSONObject)->T) { lifecycleScope.launch { try { val data=withContext(Dispatchers.IO) { request(path) }; success(data) } catch (e: Exception) { toast("Anslutningsfel: ${e.message ?: "servern svarar inte"}") } } }
    private fun post(path: String, body: JSONObject, success: (JSONObject)->Unit = {}) { lifecycleScope.launch { try { val data=withContext(Dispatchers.IO) { request(path,"POST",body) }; success(data) } catch(e: Exception) { toast("Fel: ${e.message}") } } }
    private fun loadImage(path: String, target: ImageView) { lifecycleScope.launch(Dispatchers.IO) { try { val bytes=URL(if(path.startsWith("/")) base+path else path).openStream().use { it.readBytes() }; val bmp=android.graphics.BitmapFactory.decodeByteArray(bytes,0,bytes.size); withContext(Dispatchers.Main) { target.setImageBitmap(bmp) } } catch (_: Exception) {} } }
    private fun label(text: String, size: Int, color: Int, bold: Boolean=false) = TextView(this).apply { this.text=text; textSize=size.toFloat(); setTextColor(color); if(bold) setTypeface(null,android.graphics.Typeface.BOLD); setPadding(0,dp(5),0,dp(5)) }
    private fun button(text: String, action: ()->Unit) = Button(this).apply { this.text=text; setTextColor(YELLOW); setBackgroundColor(PANEL); isAllCaps=false; setOnClickListener { action() } }
    private fun toast(s: String) = Toast.makeText(this,s,Toast.LENGTH_LONG).show()
    override fun onDestroy() { player?.release(); super.onDestroy() }
}
