/**
 * RUNECLAW web i18n — a tiny, dependency-free client localizer.
 *
 * Text is marked up in HTML with data attributes and swapped at runtime:
 *   <h2 data-i18n="hero.tagline">Intelligent · Adaptive · Relentless</h2>
 *   <input data-i18n-attr="placeholder:auth.email_ph;aria-label:auth.email">
 *   <h1 data-i18n-html="hero.h1">An AI engine you can <span>talk to.</span></h1>
 * The English text stays in the markup as the source-of-truth fallback, so an
 * un-keyed or un-translated string simply renders in English — never blank.
 *
 * Language resolution: saved choice (localStorage `rc_lang`) → the browser's
 * `navigator.language` → English. Choosing a language also writes the logged-in
 * user's `prefs.lang` so the AI chat replies in it (see docs/i18n_chat_language).
 *
 * Dual-mode: the pure helpers (normalize/resolveLang/translate) are exported
 * under Node for unit tests; in the browser the module self-initializes and
 * exposes `window.RCI18N`.
 */
(function (root) {
  'use strict';

  // Languages the web UI offers. `dir: 'rtl'` flips the document for Arabic.
  var LANGS = [
    { code: 'en', name: 'English' },
    { code: 'es', name: 'Español' },
    { code: 'zh', name: '繁體中文' },
    { code: 'pt', name: 'Português' },
    { code: 'fr', name: 'Français' },
    { code: 'de', name: 'Deutsch' },
    { code: 'ar', name: 'العربية', dir: 'rtl' },
  ];
  var RTL = { ar: true };

  // key -> { en, es, zh, pt, fr, ar }. English is also the in-markup fallback.
  var STRINGS = {
    'nav.dashboard': { en: 'Dashboard', es: 'Panel', zh: '儀表板', pt: 'Painel', fr: 'Tableau de bord', ar: 'لوحة التحكم', de: 'Dashboard' },
    'nav.track': { en: 'Track record', es: 'Historial', zh: '業績記錄', pt: 'Histórico', fr: 'Historique', ar: 'السجل', de: 'Erfolgsbilanz' },
    'nav.agents': { en: 'Marketplace', es: 'Mercado', zh: '市場', pt: 'Mercado', fr: 'Place de marché', ar: 'السوق', de: 'Marktplatz' },
    'nav.strengthmap': { en: 'Strength Map', es: 'Mapa de fuerza', zh: '強度地圖', pt: 'Mapa de força', fr: 'Carte de force', ar: 'خريطة القوة', de: 'Stärkekarte' },
    'nav.proof': { en: 'Proof of PnL', es: 'Prueba de PnL', zh: 'PnL 證明', pt: 'Prova de PnL', fr: 'Preuve de PnL', ar: 'إثبات الأرباح', de: 'PnL-Nachweis' },
    'nav.guardian': { en: 'Guardian', es: 'Guardián', zh: '守護者', pt: 'Guardião', fr: 'Guardian', ar: 'الحارس', de: 'Guardian' },
    'nav.flight': { en: 'Flight Recorder', es: 'Registro de vuelo', zh: '飛行記錄儀', pt: 'Registro de voo', fr: 'Boîte noire', ar: 'مسجل الرحلة', de: 'Flugschreiber' },
    'nav.stress': { en: 'Stress Lab', es: 'Lab de estrés', zh: '壓力測試', pt: 'Lab de estresse', fr: 'Labo de stress', ar: 'مختبر الضغط', de: 'Stresslabor' },
    'nav.leaderboard': { en: 'Leaderboard', es: 'Clasificación', zh: '排行榜', pt: 'Classificação', fr: 'Classement', ar: 'المتصدرون', de: 'Bestenliste' },
    'nav.letter': { en: 'Agent Letter', es: 'Carta del agente', zh: '代理週報', pt: 'Carta do agente', fr: 'Lettre de l’agent', ar: 'رسالة الوكيل', de: 'Agenten-Brief' },
    'nav.docs': { en: 'Docs', es: 'Docs', zh: '文件', pt: 'Docs', fr: 'Docs', ar: 'الوثائق', de: 'Doku' },
    'nav.get_started': { en: 'Get started', es: 'Empezar', zh: '開始使用', pt: 'Começar', fr: 'Commencer', ar: 'ابدأ', de: 'Loslegen' },

    'hero.eyebrow': { en: 'Live · Bitget USDT-M futures', es: 'En vivo · Futuros USDT-M de Bitget', zh: '實盤 · Bitget USDT-M 合約', pt: 'Ao vivo · Futuros USDT-M da Bitget', fr: 'En direct · Futures USDT-M Bitget', ar: 'مباشر · عقود USDT-M على Bitget', de: 'Live · Bitget USDT-M Futures' },
    'hero.h1': {
      en: 'An AI trading engine<br>you can <span>talk to — and trust.</span>',
      es: 'Un motor de trading con IA<br>con el que puedes <span>hablar — y confiar.</span>',
      zh: '一個你可以<span>對話並信任</span>的<br>AI 交易引擎。',
      pt: 'Um motor de trading com IA<br>com quem você pode <span>falar — e confiar.</span>',
      fr: 'Un moteur de trading IA<br>à qui vous pouvez <span>parler — et vous fier.</span>',
      ar: 'محرك تداول بالذكاء الاصطناعي<br><span>يمكنك محادثته والوثوق به.</span>',
      de: 'Eine KI-Handelsmaschine,<br>mit der du <span>sprechen — und der du vertrauen kannst.</span>',
    },
    'hero.tagline': { en: 'Intelligent · Explainable · Guarded', es: 'Inteligente · Explicable · Protegido', zh: '智能 · 可解釋 · 有守護', pt: 'Inteligente · Explicável · Protegido', fr: 'Intelligent · Explicable · Protégé', ar: 'ذكي · قابل للتفسير · محميّ', de: 'Intelligent · Erklärbar · Abgesichert' },
    'hero.body': {
      en: 'RUNECLAW scans 800+ symbols around the clock, explains every decision, and refuses anything its risk engine doesn’t like — then proves it in a tamper-evident ledger, simulates what could break you, and blocks malicious signing. Create an account and paper-trade in your browser within a minute; go live only when you choose to.',
      es: 'RUNECLAW analiza más de 800 símbolos las 24 horas, explica cada decisión y rechaza todo lo que su motor de riesgo no apruebe — luego lo demuestra en un registro a prueba de manipulaciones, simula qué podría hundirte y bloquea firmas maliciosas. Crea una cuenta y opera en simulación desde tu navegador en un minuto: chatea con el motor, coloca operaciones con control de riesgo y pasa a real solo cuando tú lo decidas.',
      zh: 'RUNECLAW 全天候掃描 800+ 個標的，解釋每一個決策，並拒絕任何風險引擎不認可的操作——並在防篡改的帳本中加以佐證、模擬何者可能擊垮你並阻擋惡意簽署。一分鐘內即可註冊並在瀏覽器中進行模擬交易——與引擎對話、下達經風險檢查的交易，只有在你選擇時才切換至實盤。',
      pt: 'A RUNECLAW analisa mais de 800 símbolos 24 horas por dia, explica cada decisão e recusa tudo o que o seu motor de risco não aprovar — e comprova isso num registro à prova de adulteração, simula o que poderia te quebrar e bloqueia assinaturas maliciosas. Crie uma conta e faça paper trading no navegador em um minuto: converse com o motor, faça operações com controle de risco e vá para o modo real só quando você decidir.',
      fr: 'RUNECLAW analyse plus de 800 actifs en continu, explique chaque décision et refuse tout ce que son moteur de risque désapprouve — puis le prouve dans un registre inviolable, simule ce qui pourrait vous briser et bloque les signatures malveillantes. Créez un compte et tradez en simulation dans votre navigateur en une minute : discutez avec le moteur, passez des ordres contrôlés par le risque et passez en réel uniquement quand vous le décidez.',
      ar: 'يفحص RUNECLAW أكثر من 800 رمز على مدار الساعة، ويشرح كل قرار، ويرفض أي شيء لا يوافق عليه محرك المخاطر — ثم يُثبت ذلك في سجل مقاوم للعبث، ويحاكي ما قد يُطيح بك، ويمنع التوقيع الخبيث. أنشئ حسابًا وتداول تجريبيًا في متصفحك خلال دقيقة — تحدّث مع المحرك، ونفّذ صفقات مضبوطة المخاطر، وانتقل إلى التداول الحقيقي فقط عندما تختار ذلك.',
      de: 'RUNECLAW scannt rund um die Uhr über 800 Symbole, erklärt jede Entscheidung und lehnt alles ab, was die Risiko-Engine nicht überzeugt — und belegt es anschließend in einem manipulationssicheren Journal, simuliert, was dich ruinieren könnte, und blockiert bösartige Signaturen. Erstelle in einer Minute ein Konto und handle im Browser mit Spielgeld; live gehst du erst, wenn du es willst.',
    },
    'hero.cta_create': { en: 'Create free account', es: 'Crear cuenta gratis', zh: '建立免費帳戶', pt: 'Criar conta grátis', fr: 'Créer un compte gratuit', ar: 'أنشئ حسابًا مجانيًا', de: 'Kostenloses Konto erstellen' },
    'hero.cta_dashboard': { en: 'View the dashboard', es: 'Ver el panel', zh: '查看儀表板', pt: 'Ver o painel', fr: 'Voir le tableau de bord', ar: 'عرض لوحة التحكم', de: 'Dashboard ansehen' },
    'hero.free_note': { en: 'Free · paper-trade in your browser instantly · no exchange keys needed.', es: 'Gratis · opera en papel al instante en tu navegador · sin claves de exchange.', zh: '免費 · 立即在瀏覽器中模擬交易 · 無需交易所金鑰。', pt: 'Grátis · opere em conta demo no navegador na hora · sem chaves de corretora.', fr: 'Gratuit · trading papier instantané dans votre navigateur · sans clés d’exchange.', ar: 'مجانًا · تداول تجريبي فوري في متصفحك · دون مفاتيح منصة.', de: 'Kostenlos · sofort im Browser mit Spielgeld handeln · keine Exchange-Keys nötig.' },

    'sec.mkt_h': { en: 'The Strategy Agent Marketplace', es: 'El mercado de agentes de estrategia', zh: '策略代理市場', pt: 'O mercado de agentes de estratégia', fr: 'La place de marché des agents de stratégie', ar: 'سوق وكلاء الاستراتيجية', de: 'Der Marktplatz für Strategie-Agenten' },
    'sec.mkt_p': { en: 'Browse the engine’s strategy agents — each one a real preset with a verified, reproducible backtest. Follow one, copy its picks on paper, or reproduce its numbers yourself in the Lab.', es: 'Explora los agentes de estrategia del motor: cada uno es un preajuste real con un backtest verificado y reproducible. Sigue uno, copia sus selecciones en papel o reproduce sus cifras tú mismo en el Lab.', zh: '瀏覽引擎的策略代理——每一個都是帶有可驗證、可重現回測的真實預設。關注其一、以紙上交易複製其選擇，或在實驗室中親自重現其數據。', pt: 'Explore os agentes de estratégia do motor — cada um é uma predefinição real com um backtest verificado e reproduzível. Siga um, copie as escolhas dele em papel ou reproduza os números você mesmo no Lab.', fr: 'Parcourez les agents de stratégie du moteur — chacun est un préréglage réel avec un backtest vérifié et reproductible. Suivez-en un, copiez ses choix sur papier, ou reproduisez ses chiffres vous-même dans le Lab.', ar: 'تصفّح وكلاء الاستراتيجية للمحرك — كل واحد إعداد حقيقي مع اختبار خلفي موثّق وقابل لإعادة الإنتاج. تابع أحدها، وانسخ اختياراته على الورق، أو أعِد إنتاج أرقامه بنفسك في المختبر.', de: 'Durchstöbere die Strategie-Agenten der Engine — jeder ein echtes Preset mit verifiziertem, reproduzierbarem Backtest. Folge einem, kopiere seine Auswahl auf Papier oder reproduziere den Backtest selbst.' },
    'sec.mkt_loading': { en: 'Loading the agent catalogue…', es: 'Cargando el catálogo de agentes…', zh: '正在載入代理目錄…', pt: 'Carregando o catálogo de agentes…', fr: 'Chargement du catalogue d’agents…', ar: 'جارٍ تحميل كتالوج الوكلاء…', de: 'Agenten-Katalog wird geladen…' },
    'sec.mkt_cta': { en: 'Browse the full marketplace →', es: 'Explorar todo el mercado →', zh: '瀏覽完整市場 →', pt: 'Explorar todo o mercado →', fr: 'Parcourir toute la place de marché →', ar: 'تصفّح السوق كاملاً →', de: 'Kompletten Marktplatz ansehen →' },
    'sec.guardian_h': { en: 'The safety layer AI trading forgot', es: 'La capa de seguridad que el trading con IA olvidó', zh: 'AI 交易遺忘的安全層', pt: 'A camada de segurança que o trading com IA esqueceu', fr: 'La couche de sécurité que le trading par IA a oubliée', ar: 'طبقة الأمان التي نسيها التداول بالذكاء الاصطناعي', de: 'Die Sicherheitsebene, die KI-Trading vergessen hat' },
    'sec.guardian_p': { en: 'Most bots only chase returns. Guardian makes an autonomous agent <b>trustworthy</b> — it proves every decision, simulates what breaks you, watches the market for systemic risk, blocks malicious signing, and plans your way out.', es: 'La mayoría de los bots solo persiguen rentabilidad. Guardian hace que un agente autónomo sea <b>confiable</b>: prueba cada decisión, simula qué te rompe, vigila el riesgo sistémico del mercado, bloquea firmas maliciosas y planifica tu salida.', zh: '多數機器人只追逐收益。Guardian 讓自主代理<b>值得信任</b>——它證明每個決策、模擬何者會擊垮你、監視市場的系統性風險、阻擋惡意簽署，並規劃你的退場。', pt: 'A maioria dos bots só busca retorno. O Guardian torna um agente autônomo <b>confiável</b>: prova cada decisão, simula o que te quebra, vigia o risco sistêmico do mercado, bloqueia assinaturas maliciosas e planeja sua saída.', fr: 'La plupart des bots ne visent que le rendement. Guardian rend un agent autonome <b>digne de confiance</b> : il prouve chaque décision, simule ce qui vous brise, surveille le risque systémique du marché, bloque les signatures malveillantes et planifie votre sortie.', ar: 'معظم الروبوتات تطارد العوائد فقط. يجعل Guardian الوكيل المستقل <b>جديرًا بالثقة</b> — يُثبت كل قرار، ويحاكي ما قد يُطيح بك، ويراقب المخاطر النظامية في السوق، ويمنع التوقيع الخبيث، ويخطط لخروجك.', de: 'Die meisten Bots jagen nur Rendite. Guardian macht einen autonomen Agenten <b>vertrauenswürdig</b> — er belegt jede Entscheidung, simuliert, was dich ruiniert, überwacht den Markt auf systemisches Risiko, blockiert bösartige Signaturen und plant deinen Ausweg. Die KI schlägt vor · deterministische Kontrollen genehmigen · die Wallet setzt durch · der Recorder beweist · der Fluchtagent rettet.' },
    'sec.guardian_cta': { en: 'Explore Guardian →', es: 'Explorar Guardian →', zh: '探索 Guardian →', pt: 'Explorar o Guardian →', fr: 'Découvrir Guardian →', ar: 'استكشف Guardian →', de: 'Guardian entdecken →' },
    'sec.strength_eyebrow': { en: 'Market intelligence', es: 'Inteligencia de mercado', zh: '市場情報', pt: 'Inteligência de mercado', fr: 'Intelligence de marché', ar: 'استخبارات السوق', de: 'Marktintelligenz' },
    'sec.strength_h': { en: 'See the whole market in 3D', es: 'Ve todo el mercado en 3D', zh: '以 3D 縱覽整個市場', pt: 'Veja o mercado inteiro em 3D', fr: 'Voyez tout le marché en 3D', ar: 'شاهد السوق بالكامل ثلاثي الأبعاد', de: 'Sieh den ganzen Markt in 3D' },
    'sec.strength_p': { en: 'Every Bitget USDT-perp plotted by momentum, funding and open interest, coloured by long-vs-short strength — the entire universe at a glance. Orbit it, tap a coin for its factor breakdown, then open the trade on your CEX or DEX of choice.', es: 'Cada perpetuo USDT de Bitget trazado por momentum, funding e interés abierto, coloreado por fuerza long-vs-short: todo el universo de un vistazo. Órbita, toca una moneda para ver su desglose de factores y abre la operación en el CEX o DEX que elijas.', zh: '每個 Bitget USDT 永續合約依動能、資金費率與未平倉量繪製，並以多空強度著色——整個市場一目了然。旋轉檢視、點選任一幣種查看其因子拆解，然後在你選擇的 CEX 或 DEX 上開倉。', pt: 'Cada perpétuo USDT da Bitget plotado por momentum, funding e open interest, colorido por força long-vs-short — o universo inteiro num relance. Orbite, toque numa moeda para ver o detalhamento de fatores e abra a operação no CEX ou DEX que preferir.', fr: 'Chaque perpétuel USDT de Bitget tracé par momentum, funding et open interest, coloré par la force long-vs-short — tout l’univers en un coup d’œil. Orbitez, touchez une pièce pour son détail de facteurs, puis ouvrez la position sur le CEX ou DEX de votre choix.', ar: 'كل عقد USDT دائم على Bitget مرسوم حسب الزخم والتمويل والفائدة المفتوحة، وملوّن بقوة الشراء مقابل البيع — الكون بأكمله في لمحة. دوّره، وانقر على أي عملة لتفصيل عواملها، ثم افتح الصفقة على منصة CEX أو DEX التي تختارها.', de: 'Jeder USDT-Perp von Bitget nach Momentum, Funding und Open Interest aufgetragen, eingefärbt nach Long-gegen-Short-Stärke — das gesamte Universum auf einen Blick. Dreh es, tippe eine Coin für ihre Faktor-Aufschlüsselung an und eröffne den Trade auf der CEX oder DEX deiner Wahl.' },
    'sec.strength_cta': { en: '🌐 Open the 3D Strength Map →', es: '🌐 Abrir el mapa de fuerza 3D →', zh: '🌐 開啟 3D 強度地圖 →', pt: '🌐 Abrir o mapa de força 3D →', fr: '🌐 Ouvrir la carte de force 3D →', ar: '🌐 افتح خريطة القوة ثلاثية الأبعاد →', de: '🌐 3D-Stärkekarte öffnen →' },
    'sec.why_h': { en: 'Why RUNECLAW is different', es: 'Por qué RUNECLAW es diferente', zh: 'RUNECLAW 有何不同', pt: 'Por que a RUNECLAW é diferente', fr: 'Pourquoi RUNECLAW est différent', ar: 'لماذا يختلف RUNECLAW', de: 'Warum RUNECLAW anders ist' },
    'sec.why_p': { en: 'Most bots ask you to trust a black box. RUNECLAW is built to earn it.', es: 'La mayoría de los bots te piden confiar en una caja negra. RUNECLAW está hecho para ganarse esa confianza.', zh: '多數機器人要你信任一個黑箱。RUNECLAW 則是為了贏得信任而打造。', pt: 'A maioria dos bots pede que você confie numa caixa-preta. A RUNECLAW é feita para conquistar essa confiança.', fr: 'La plupart des bots vous demandent de faire confiance à une boîte noire. RUNECLAW est conçu pour la mériter.', ar: 'معظم الروبوتات تطلب منك الوثوق بصندوق أسود. أما RUNECLAW فقد صُمِّم ليكسب ثقتك.', de: 'Die meisten Bots verlangen Vertrauen in eine Blackbox. RUNECLAW ist gebaut, um es sich zu verdienen.' },
    // /arena — the growth page, localized for global competitions.
    'arena.h1': { en: 'Paper Trading Arena', es: 'Arena de Trading en Papel', zh: '模擬交易競技場', pt: 'Arena de Trading Simulado', fr: 'Arène de Trading Papier', ar: 'ساحة التداول الورقي', de: 'Papier-Handelsarena' },
    'arena.lede': {
      en: 'Every account starts with the same <b>10,000 vUSDT virtual stake</b> — no exchange API keys, no setup, no risk. Fills and marks use <b>real live Bitget prices</b>, liquidations are enforced exactly like the Stress Lab models them, and the public board ranks <b>percent return</b> under anonymous handles. Prove your edge before you ever connect a real account — and when paper competitions run, this is where they\'ll happen.',
      es: 'Cada cuenta empieza con el mismo <b>capital virtual de 10.000 vUSDT</b> — sin claves API, sin configuración, sin riesgo. Las ejecuciones usan <b>precios reales de Bitget en vivo</b>, las liquidaciones se aplican exactamente como las modela el Stress Lab, y la clasificación pública ordena por <b>retorno porcentual</b> bajo alias anónimos. Demuestra tu ventaja antes de conectar una cuenta real — y cuando haya competiciones, será aquí.',
      zh: '每個帳戶都以相同的 <b>10,000 vUSDT 虛擬資金</b>起步——無需交易所 API 密鑰、無需設定、零風險。成交與標記價採用 <b>Bitget 實時真實價格</b>，強平機制與壓力實驗室的模型完全一致，公開排行榜以匿名代號按<b>回報百分比</b>排名。在連接真實帳戶之前先證明你的實力——模擬交易比賽也將在這裡舉行。',
      pt: 'Cada conta começa com o mesmo <b>capital virtual de 10.000 vUSDT</b> — sem chaves API, sem configuração, sem risco. As execuções usam <b>preços reais da Bitget ao vivo</b>, as liquidações seguem exatamente o modelo do Stress Lab, e o ranking público ordena por <b>retorno percentual</b> sob alias anónimos. Prove a sua vantagem antes de conectar uma conta real — e quando houver competições, será aqui.',
      fr: 'Chaque compte démarre avec la même <b>mise virtuelle de 10 000 vUSDT</b> — sans clés API, sans configuration, sans risque. Les exécutions utilisent les <b>vrais prix Bitget en direct</b>, les liquidations suivent exactement le modèle du Stress Lab, et le classement public trie par <b>rendement en pourcentage</b> sous pseudonymes anonymes. Prouvez votre avantage avant de connecter un vrai compte — et les compétitions papier se joueront ici.',
      ar: 'يبدأ كل حساب بنفس <b>رأس المال الافتراضي 10,000 vUSDT</b> — بلا مفاتيح API، بلا إعداد، بلا مخاطرة. تُنفَّذ الصفقات بأسعار <b>Bitget الحقيقية المباشرة</b>، وتُطبَّق التصفية تمامًا كما يحاكيها مختبر الضغط، وتُرتَّب اللوحة العامة حسب <b>نسبة العائد</b> بأسماء مستعارة. أثبت مهارتك قبل ربط حساب حقيقي — وعندما تُقام المسابقات فهنا مكانها.',
      de: 'Jedes Konto startet mit demselben <b>virtuellen Einsatz von 10.000 vUSDT</b> — keine Exchange-API-Keys, keine Einrichtung, kein Risiko. Ausführungen und Marks nutzen <b>echte Live-Preise von Bitget</b>, Liquidationen greifen exakt so, wie das Stresslabor sie modelliert, und die öffentliche Bestenliste rangiert nach <b>Prozentrendite</b> unter anonymen Namen. Beweise deinen Vorsprung, bevor du je ein echtes Konto verbindest — und wenn Papier-Wettbewerbe laufen, finden sie genau hier statt.',
    },
    'arena.p_account': { en: 'Your paper account', es: 'Tu cuenta de papel', zh: '你的模擬帳戶', pt: 'A sua conta simulada', fr: 'Votre compte papier', ar: 'حسابك الورقي', de: 'Dein Papierkonto' },
    'arena.p_ticket': { en: 'Open a paper position', es: 'Abrir una posición de papel', zh: '開立模擬倉位', pt: 'Abrir uma posição simulada', fr: 'Ouvrir une position papier', ar: 'افتح مركزًا ورقيًا', de: 'Papierposition eröffnen' },
    'arena.p_positions': { en: 'Open positions', es: 'Posiciones abiertas', zh: '持倉中', pt: 'Posições abertas', fr: 'Positions ouvertes', ar: 'المراكز المفتوحة', de: 'Offene Positionen' },
    'arena.p_history': { en: 'Closed trades', es: 'Operaciones cerradas', zh: '已平倉交易', pt: 'Operações fechadas', fr: 'Trades clôturés', ar: 'الصفقات المغلقة', de: 'Geschlossene Trades' },
    'arena.p_board': { en: 'Arena leaderboard', es: 'Clasificación de la Arena', zh: '競技場排行榜', pt: 'Ranking da Arena', fr: 'Classement de l\'Arène', ar: 'لوحة صدارة الساحة', de: 'Arena-Bestenliste' },
    'arena.p_season': { en: 'Competition season', es: 'Temporada de competición', zh: '比賽賽季', pt: 'Temporada de competição', fr: 'Saison de compétition', ar: 'موسم المنافسة', de: 'Wettbewerbssaison' },
    'arena.p_tape': { en: 'Live tape', es: 'Cinta en vivo', zh: '即時成交帶', pt: 'Fita ao vivo', fr: 'Bande en direct', ar: 'الشريط المباشر', de: 'Live-Ticker' },
    'arena.tape_empty': { en: 'Quiet tape — nothing closed yet. Be the first print of the day.', es: 'Cinta tranquila — nada cerrado aún. Sé la primera operación del día.', zh: '成交帶安靜——尚無平倉。成為今天的第一筆吧。', pt: 'Fita calma — nada fechado ainda. Seja a primeira operação do dia.', fr: 'Bande calme — rien de clôturé pour l\'instant. Soyez le premier trade du jour.', ar: 'الشريط هادئ — لا صفقات مغلقة بعد. كن أول صفقة اليوم.', de: 'Ruhiger Ticker — noch nichts geschlossen. Sei der erste Abschluss des Tages.' },
    'arena.p_follow': { en: 'Practice-follow the engine', es: 'Sigue al motor en modo práctica', zh: '跟隨引擎練習', pt: 'Siga o motor em modo prática', fr: 'Suivre le moteur en entraînement', ar: 'تابع المحرك تدريبيًا', de: 'Der Engine zur Übung folgen' },
    'arena.p_hall': { en: 'Hall of Champions', es: 'Salón de Campeones', zh: '冠軍殿堂', pt: 'Salão dos Campeões', fr: 'Panthéon des Champions', ar: 'قاعة الأبطال', de: 'Halle der Champions' },
    'arena.b_open': { en: 'Open paper position', es: 'Abrir posición de papel', zh: '開立模擬倉位', pt: 'Abrir posição simulada', fr: 'Ouvrir la position papier', ar: 'افتح المركز الورقي', de: 'Papierposition eröffnen' },
    'arena.b_join': { en: 'Join the board', es: 'Unirse a la clasificación', zh: '加入排行榜', pt: 'Entrar no ranking', fr: 'Rejoindre le classement', ar: 'انضم إلى اللوحة', de: 'Der Bestenliste beitreten' },
    // Arena DYNAMIC strings (built in JS): {x} placeholders are filled client-side.
    'arena.d_tape_pulse': { en: '{n} traders · {m} closes in 24h', es: '{n} traders · {m} cierres en 24h', zh: '{n} 位交易者 · 24小時內 {m} 筆平倉', pt: '{n} traders · {m} fechamentos em 24h', fr: '{n} traders · {m} clôtures en 24h', ar: '{n} متداولًا · {m} إغلاقًا خلال 24 ساعة', de: '{n} Trader · {m} Abschlüsse in 24 Std.' },
    'arena.d_reason_tp': { en: '🎯 target', es: '🎯 objetivo', zh: '🎯 止盈', pt: '🎯 alvo', fr: '🎯 objectif', ar: '🎯 الهدف', de: '🎯 Ziel' },
    'arena.d_reason_sl': { en: '🛡 stop', es: '🛡 stop', zh: '🛡 止損', pt: '🛡 stop', fr: '🛡 stop', ar: '🛡 وقف', de: '🛡 Stopp' },
    'arena.d_reason_liq': { en: '💀 liquidated', es: '💀 liquidado', zh: '💀 爆倉', pt: '💀 liquidado', fr: '💀 liquidé', ar: '💀 مُصفّى', de: '💀 liquidiert' },
    'arena.d_reason_closed': { en: 'closed', es: 'cerrado', zh: '已平倉', pt: 'fechado', fr: 'clôturé', ar: 'مغلق', de: 'geschlossen' },
    'arena.d_streak': { en: '🔥 {n}-day streak', es: '🔥 racha de {n} días', zh: '🔥 連續 {n} 天', pt: '🔥 sequência de {n} dias', fr: '🔥 série de {n} jours', ar: '🔥 سلسلة {n} أيام', de: '🔥 {n}-Tage-Serie' },
    'arena.d_streak_best': { en: '· best {n}', es: '· récord {n}', zh: '· 最佳 {n}', pt: '· recorde {n}', fr: '· record {n}', ar: '· الأفضل {n}', de: '· Bestwert {n}' },
    'arena.d_streak_keep': { en: '· close today to keep it', es: '· cierra hoy para mantenerla', zh: '· 今天平倉以保持', pt: '· feche hoje para mantê-la', fr: '· clôturez aujourd’hui pour la garder', ar: '· أغلق صفقة اليوم للحفاظ عليها', de: '· heute schließen, um sie zu halten' },
    'arena.d_streak_title': { en: 'Consecutive days with a closed trade (UTC). Best: {n}', es: 'Días consecutivos con una operación cerrada (UTC). Récord: {n}', zh: '連續有平倉交易的天數（UTC）。最佳：{n}', pt: 'Dias consecutivos com uma operação fechada (UTC). Recorde: {n}', fr: 'Jours consécutifs avec une clôture (UTC). Record : {n}', ar: 'أيام متتالية بصفقة مغلقة (UTC). الأفضل: {n}', de: 'Aufeinanderfolgende Tage mit einem geschlossenen Trade (UTC). Bestwert: {n}' },
    'arena.d_quests_h': { en: 'Weekly quests · reset Monday 00:00 UTC', es: 'Misiones semanales · se reinician el lunes 00:00 UTC', zh: '每週任務 · 週一 00:00 UTC 重置', pt: 'Missões semanais · reiniciam segunda 00:00 UTC', fr: 'Quêtes hebdomadaires · réinitialisées lundi 00:00 UTC', ar: 'مهام أسبوعية · تُعاد الاثنين 00:00 UTC', de: 'Wochenaufgaben · Reset montags 00:00 UTC' },
    'arena.q_five_closes': { en: 'Close 5 trades', es: 'Cierra 5 operaciones', zh: '平倉 5 筆交易', pt: 'Feche 5 operações', fr: 'Clôturez 5 trades', ar: 'أغلق 5 صفقات', de: '5 Trades schließen' },
    'arena.q_three_tp': { en: 'Land 3 take-profit exits', es: 'Logra 3 salidas por take-profit', zh: '達成 3 次止盈離場', pt: 'Consiga 3 saídas por take-profit', fr: 'Réussissez 3 sorties en take-profit', ar: 'حقّق 3 خروجات بجني الأرباح', de: '3 Take-Profit-Ausstiege erzielen' },
    'arena.q_three_symbols': { en: 'Trade 3 different symbols', es: 'Opera 3 símbolos distintos', zh: '交易 3 個不同幣種', pt: 'Opere 3 símbolos diferentes', fr: 'Tradez 3 symboles différents', ar: 'تداول 3 رموز مختلفة', de: '3 verschiedene Symbole handeln' },
    'arena.q_three_wins': { en: 'Win 3 trades', es: 'Gana 3 operaciones', zh: '贏得 3 筆交易', pt: 'Vença 3 operações', fr: 'Gagnez 3 trades', ar: 'اربح 3 صفقات', de: '3 Trades gewinnen' },
    'arena.q_survive_zero': { en: 'A week with zero liquidations (min 3 closes)', es: 'Una semana sin liquidaciones (mín. 3 cierres)', zh: '一週零爆倉（至少 3 筆平倉）', pt: 'Uma semana sem liquidações (mín. 3 fechamentos)', fr: 'Une semaine sans liquidation (min. 3 clôtures)', ar: 'أسبوع بلا تصفيات (3 إغلاقات على الأقل)', de: 'Eine Woche ohne Liquidationen (mind. 3 Abschlüsse)' },
    'arena.q_planned_exit': { en: 'Close 2 trades by your own TP or SL', es: 'Cierra 2 operaciones con tu propio TP o SL', zh: '用你自己的 TP 或 SL 平倉 2 筆', pt: 'Feche 2 operações pelo seu próprio TP ou SL', fr: 'Clôturez 2 trades via votre propre TP ou SL', ar: 'أغلق صفقتين عبر TP أو SL الخاص بك', de: '2 Trades über eigenes TP oder SL schließen' },
    'arena.d_quest_done': { en: '🎉 Quest complete:', es: '🎉 ¡Misión completada!', zh: '🎉 任務完成：', pt: '🎉 Missão concluída:', fr: '🎉 Quête accomplie :', ar: '🎉 اكتملت المهمة:', de: '🎉 Aufgabe erfüllt:' },
    'arena.d_chart_loading': { en: 'Loading chart & engine read…', es: 'Cargando gráfico y lectura del motor…', zh: '載入圖表與引擎解讀…', pt: 'Carregando gráfico e leitura do motor…', fr: 'Chargement du graphique et de la lecture du moteur…', ar: 'جارٍ تحميل الرسم وقراءة المحرك…', de: 'Chart & Engine-Lesung werden geladen…' },
    'arena.d_chart_unavail': { en: 'Market candles unavailable right now — try again shortly.', es: 'Velas de mercado no disponibles ahora — inténtalo de nuevo en breve.', zh: '目前無法取得市場K線——請稍後再試。', pt: 'Velas de mercado indisponíveis agora — tente novamente em breve.', fr: 'Bougies de marché indisponibles — réessayez sous peu.', ar: 'شموع السوق غير متاحة الآن — حاول مجددًا بعد قليل.', de: 'Marktkerzen derzeit nicht verfügbar — versuche es gleich erneut.' },
    'arena.d_hist_loading': { en: 'Loading how it played out…', es: 'Cargando cómo terminó…', zh: '載入交易過程…', pt: 'Carregando como terminou…', fr: 'Chargement du déroulé…', ar: 'جارٍ تحميل ما حدث…', de: 'Wird geladen, wie es ausging…' },
    'arena.d_pat_unavail': { en: 'engine pattern read unavailable right now', es: 'lectura de patrones del motor no disponible ahora', zh: '引擎形態解讀目前不可用', pt: 'leitura de padrões do motor indisponível agora', fr: 'lecture des patterns du moteur indisponible', ar: 'قراءة أنماط المحرك غير متاحة الآن', de: 'Formationslesung der Engine derzeit nicht verfügbar' },
    'arena.d_chart_note': { en: '{tf} candles · VWAP band + structure computed with the engine’s formulas · patterns read live from the engine (4h) · not advice', es: 'velas {tf} · banda VWAP + estructura con las fórmulas del motor · patrones leídos en vivo del motor (4h) · no es asesoramiento', zh: '{tf} K線 · VWAP 帶與結構以引擎公式計算 · 形態由引擎實時讀取（4h）· 非投資建議', pt: 'velas {tf} · banda VWAP + estrutura com as fórmulas do motor · padrões lidos ao vivo do motor (4h) · não é aconselhamento', fr: 'bougies {tf} · bande VWAP + structure via les formules du moteur · patterns lus en direct du moteur (4h) · pas un conseil', ar: 'شموع {tf} · نطاق VWAP والبنية بمعادلات المحرك · الأنماط تُقرأ مباشرة من المحرك (4h) · ليست نصيحة', de: '{tf}-Kerzen · VWAP-Band + Struktur mit den Formeln der Engine berechnet · Formationen live aus der Engine (4h) · keine Anlageberatung' },
    'arena.d_close_failed': { en: 'Close failed — try again.', es: 'No se pudo cerrar — inténtalo de nuevo.', zh: '平倉失敗——請重試。', pt: 'Falha ao fechar — tente novamente.', fr: 'Échec de la clôture — réessayez.', ar: 'فشل الإغلاق — حاول مجددًا.', de: 'Schließen fehlgeschlagen — bitte erneut versuchen.' },
    'arena.d_closed_msg': { en: 'Closed {s} — PnL {p} vUSDT', es: 'Cerrado {s} — PnL {p} vUSDT', zh: '已平倉 {s} — 損益 {p} vUSDT', pt: 'Fechado {s} — PnL {p} vUSDT', fr: 'Clôturé {s} — PnL {p} vUSDT', ar: 'أُغلق {s} — الربح/الخسارة {p} vUSDT', de: '{s} geschlossen — PnL {p} vUSDT' },
    'arena.d_seal_title': { en: '{n} of {m} closes carry a verifiable open-time receipt — open the card to check the hashes yourself', es: '{n} de {m} cierres llevan un recibo verificable del momento de apertura — abre la ficha y comprueba los hashes tú mismo', zh: '{m} 筆平倉中有 {n} 筆帶有可驗證的開倉時收據——打開卡片自行核對雜湊', pt: '{n} de {m} fechamentos têm um recibo verificável do momento da abertura — abra o cartão e confira os hashes você mesmo', fr: '{n} clôtures sur {m} portent un reçu vérifiable émis à l’ouverture — ouvrez la carte et vérifiez les hash vous-même', ar: '{n} من {m} إغلاقًا تحمل إيصالًا قابلًا للتحقق صادرًا عند الفتح — افتح البطاقة وتحقّق من التجزئات بنفسك', de: '{n} von {m} Abschlüssen tragen einen überprüfbaren Beleg vom Eröffnungszeitpunkt — öffne die Karte und prüfe die Hashes selbst' },
    'arena.d_pick_handle': { en: 'Pick a handle first.', es: 'Primero elige un alias.', zh: '請先選擇暱稱。', pt: 'Escolha um apelido primeiro.', fr: 'Choisissez d’abord un pseudo.', ar: 'اختر اسمًا مستعارًا أولًا.', de: 'Wähle zuerst einen Namen.' },
    // Provable Calls trust section on the landing page.
    'sec.prov_eyebrow': { en: 'Provable Calls', es: 'Llamadas Verificables', zh: '可證明的交易呼叫', pt: 'Chamadas Verificáveis', fr: 'Appels Prouvables', ar: 'نداءات قابلة للإثبات', de: 'Beweisbare Calls' },
    'sec.prov_h': { en: "Don't trust the screenshot. Verify the call.", es: 'No confíes en la captura. Verifica la llamada.', zh: '別信截圖。驗證這筆呼叫。', pt: 'Não confie no print. Verifique a chamada.', fr: 'Ne croyez pas la capture. Vérifiez l’appel.', ar: 'لا تثق بلقطة الشاشة. تحقّق من النداء.', de: 'Glaub dem Screenshot nicht. Überprüfe den Call.' },
    'sec.prov_p': { en: "Every call the engine makes — and every paper trade in the Arena — is hashed the moment it's made, <b>before the market moves</b>. The outcome attaches to that same sealed record later and can never change it. No backdated wins, no quietly deleted losers.", es: 'Cada llamada del motor — y cada operación en papel de la Arena — se cifra en el momento en que se hace, <b>antes de que el mercado se mueva</b>. El resultado se adjunta después a ese mismo registro sellado y jamás puede alterarlo. Sin ganancias retroactivas, sin perdedoras borradas en silencio.', zh: '引擎發出的每一筆呼叫——以及競技場的每一筆模擬交易——都在做出的當下被雜湊，<b>早於市場變動</b>。結果稍後附加到同一份密封紀錄，永遠無法更改它。沒有事後補登的勝利，也沒有被悄悄刪掉的虧損。', pt: 'Cada chamada do motor — e cada operação simulada na Arena — é hasheada no instante em que é feita, <b>antes de o mercado se mexer</b>. O resultado se anexa depois a esse mesmo registro selado e nunca pode alterá-lo. Sem vitórias retroativas, sem perdas apagadas em silêncio.', fr: 'Chaque appel du moteur — et chaque trade papier de l’Arène — est haché à l’instant où il est émis, <b>avant que le marché ne bouge</b>. Le résultat s’attache ensuite au même enregistrement scellé et ne peut jamais le modifier. Aucun gain antidaté, aucune perte discrètement supprimée.', ar: 'كل نداء يصدره المحرك — وكل صفقة ورقية في الحلبة — يُجزَّأ لحظة إصداره، <b>قبل أن يتحرك السوق</b>. تُرفق النتيجة لاحقًا بالسجل المختوم نفسه ولا يمكنها تغييره أبدًا. لا مكاسب بأثر رجعي، ولا خسائر تُحذف بصمت.', de: 'Jeder Call der Engine — und jeder Papier-Trade in der Arena — wird im Moment seiner Entstehung gehasht, <b>bevor sich der Markt bewegt</b>. Das Ergebnis wird später an denselben versiegelten Datensatz gehängt und kann ihn nie verändern. Keine rückdatierten Gewinne, keine still gelöschten Verlierer.' },
    'sec.prov_mirror': { en: "Copy that hash anywhere public and the whole day is frozen for everyone — we can't add a call to it afterwards without breaking your copy.", es: 'Copia ese hash en cualquier sitio público y el día entero queda congelado para todos: no podremos añadirle una llamada después sin romper tu copia.', zh: '把該雜湊複製到任何公開之處，整天的紀錄就對所有人凍結——我們無法事後往裡面加入呼叫而不破壞你的副本。', pt: 'Copie esse hash em qualquer lugar público e o dia inteiro fica congelado para todos — não podemos acrescentar uma chamada depois sem quebrar a sua cópia.', fr: 'Copiez ce hash n’importe où publiquement et la journée entière est figée pour tous — nous ne pouvons plus y ajouter un appel sans casser votre copie.', ar: 'انسخ هذه التجزئة في أي مكان عام ويتجمّد اليوم بأكمله للجميع — لا يمكننا إضافة نداء إليه لاحقًا دون كسر نسختك.', de: 'Kopiere diesen Hash an einen öffentlichen Ort und der ganze Tag ist für alle eingefroren — wir können danach keinen Call mehr hinzufügen, ohne deine Kopie zu brechen.' },
    'sec.prov_root_head': { en: 'Sealed on {day} · {n} calls committed to this one hash', es: 'Sellado el {day} · {n} llamadas comprometidas en este único hash', zh: '{day} 封存 · {n} 筆呼叫承諾於這一個雜湊', pt: 'Selado em {day} · {n} chamadas comprometidas neste único hash', fr: 'Scellé le {day} · {n} appels engagés dans ce seul hash', ar: 'خُتم في {day} · {n} نداءً ملتزمًا بهذه التجزئة الواحدة', de: 'Versiegelt am {day} · {n} Calls in diesem einen Hash festgeschrieben' },
    'dp.radar3d': { en: 'Sector sweep — live 3D radar', es: 'Barrido sectorial — radar 3D en vivo', zh: '板塊掃描——即時 3D 雷達', pt: 'Varredura setorial — radar 3D ao vivo', fr: 'Balayage sectoriel — radar 3D en direct', ar: 'مسح القطاعات — رادار ثلاثي الأبعاد مباشر', de: 'Sektor-Sweep — Live-3D-Radar' },
    'dp.rwa': { en: 'RWA & on-chain radar', es: 'Radar RWA y on-chain', zh: 'RWA 與鏈上雷達', pt: 'Radar RWA e on-chain', fr: 'Radar RWA et on-chain', ar: 'رادار الأصول الواقعية والسلسلة', de: 'RWA- & On-Chain-Radar' },
    'dp.airdrops': { en: 'Airdrop & testnet radar', es: 'Radar de airdrops y testnets', zh: '空投與測試網雷達', pt: 'Radar de airdrops e testnets', fr: 'Radar airdrops et testnets', ar: 'رادار الإنزالات والشبكات التجريبية', de: 'Airdrop- & Testnet-Radar' },
    'dp.meme': { en: 'Meme & AI-token radar', es: 'Radar de memes y tokens de IA', zh: '迷因與 AI 代幣雷達', pt: 'Radar de memes e tokens de IA', fr: 'Radar mèmes et tokens IA', ar: 'رادار عملات الميم ورموز الذكاء الاصطناعي', de: 'Meme- & KI-Token-Radar' },
    'dp.flow': { en: 'On-chain flow — DEX taker balance', es: 'Flujo on-chain — balance de tomadores en DEX', zh: '鏈上資金流——DEX 主動成交平衡', pt: 'Fluxo on-chain — balanço de takers em DEX', fr: 'Flux on-chain — balance des takers DEX', ar: 'التدفق على السلسلة — توازن المنفّذين في DEX', de: 'On-Chain-Fluss — DEX-Taker-Bilanz' },
    'dp.router': { en: 'Venue router — cheapest exchange per pair', es: 'Enrutador de venues — el exchange más barato por par', zh: '場所路由——每個交易對最便宜的交易所', pt: 'Roteador de venues — a exchange mais barata por par', fr: 'Routeur de plateformes — l\'exchange le moins cher par paire', ar: 'موجّه المنصات — أرخص منصة لكل زوج', de: 'Venue-Router — günstigste Börse je Paar' },
    'dp.mkpat': { en: 'Engine pattern read', es: 'Lectura de patrones del motor', zh: '引擎形態解讀', pt: 'Leitura de padrões do motor', fr: 'Lecture des patterns du moteur', ar: 'قراءة أنماط المحرك', de: 'Formationslesung der Engine' },
    'dp.universe': { en: 'Universe', es: 'Universo', zh: '全市場', pt: 'Universo', fr: 'Univers', ar: 'الكون', de: 'Universum' },
    'dp.stream': { en: 'Signal stream', es: 'Flujo de señales', zh: '訊號串流', pt: 'Fluxo de sinais', fr: 'Flux de signaux', ar: 'تدفق الإشارات', de: 'Signalstrom' },
    'dp.spat': { en: 'Pattern read', es: 'Lectura de patrones', zh: '形態解讀', pt: 'Leitura de padrões', fr: 'Lecture des patterns', ar: 'قراءة الأنماط', de: 'Formationslesung' },
    'dp.sinsights': { en: 'What works', es: 'Qué funciona', zh: '什麼有效', pt: 'O que funciona', fr: 'Ce qui marche', ar: 'ما الذي ينجح', de: 'Was funktioniert' },
    'dp.dscards': { en: 'Deep Scan', es: 'Escaneo profundo', zh: '深度掃描', pt: 'Varredura profunda', fr: 'Scan profond', ar: 'المسح العميق', de: 'Tiefenscan' },
    'dp.dslook': { en: 'Check any symbol', es: 'Consulta cualquier símbolo', zh: '查詢任一幣種', pt: 'Consulte qualquer símbolo', fr: 'Vérifiez n\'importe quel symbole', ar: 'افحص أي رمز', de: 'Beliebiges Symbol prüfen' },
    'dp.curve': { en: 'Equity curve', es: 'Curva de capital', zh: '權益曲線', pt: 'Curva de capital', fr: 'Courbe de capital', ar: 'منحنى رأس المال', de: 'Kapitalkurve' },
    'dp.breakdown': { en: 'By symbol', es: 'Por símbolo', zh: '依幣種', pt: 'Por símbolo', fr: 'Par symbole', ar: 'حسب الرمز', de: 'Nach Symbol' },
    'dp.cal': { en: 'Daily PnL — last 4 weeks', es: 'PnL diario — últimas 4 semanas', zh: '每日損益——近 4 週', pt: 'PnL diário — últimas 4 semanas', fr: 'PnL quotidien — 4 dernières semaines', ar: 'الأرباح اليومية — آخر 4 أسابيع', de: 'Tägliche PnL — letzte 4 Wochen' },
    'dp.edge': { en: 'Edge metrics — the numbers pro desks track', es: 'Métricas de ventaja — las cifras que siguen las mesas profesionales', zh: '優勢指標——專業交易台追蹤的數字', pt: 'Métricas de edge — os números que as mesas profissionais acompanham', fr: 'Métriques d\'edge — les chiffres suivis par les desks pros', ar: 'مقاييس الأفضلية — الأرقام التي تتابعها المكاتب المحترفة', de: 'Edge-Kennzahlen — die Zahlen, die Profi-Desks verfolgen' },
    'dp.hist': { en: 'Trade history & journal', es: 'Historial de operaciones y diario', zh: '交易歷史與日誌', pt: 'Histórico de operações e diário', fr: 'Historique des trades et journal', ar: 'سجل الصفقات والمفكرة', de: 'Handelshistorie & Journal' },
    'dp.eregime': { en: 'Market regime', es: 'Régimen de mercado', zh: '市場狀態', pt: 'Regime de mercado', fr: 'Régime de marché', ar: 'نظام السوق', de: 'Marktregime' },
    'dp.ecb': { en: 'Engine account', es: 'Cuenta del motor', zh: '引擎帳戶', pt: 'Conta do motor', fr: 'Compte du moteur', ar: 'حساب المحرك', de: 'Engine-Konto' },
    'dp.emods': { en: 'Engine modules', es: 'Módulos del motor', zh: '引擎模組', pt: 'Módulos do motor', fr: 'Modules du moteur', ar: 'وحدات المحرك', de: 'Engine-Module' },
    'dp.ecards': { en: 'Engine\'s current setups', es: 'Setups actuales del motor', zh: '引擎當前佈局', pt: 'Setups atuais do motor', fr: 'Setups actuels du moteur', ar: 'إعدادات المحرك الحالية', de: 'Aktuelle Setups der Engine' },
    'dp.eshadow': { en: 'Shadow book — what the gates cost', es: 'Libro sombra — lo que cuestan los filtros', zh: '影子帳本——關卡的代價', pt: 'Livro sombra — o que os filtros custam', fr: 'Livre fantôme — ce que coûtent les filtres', ar: 'الدفتر الظلّي — ما تكلّفه البوابات', de: 'Schattenbuch — was die Filter kosten' },
    'dp.elist': { en: 'New listings radar', es: 'Radar de nuevos listados', zh: '新上架雷達', pt: 'Radar de novas listagens', fr: 'Radar des nouvelles cotations', ar: 'رادار الإدراجات الجديدة', de: 'Radar für Neulistungen' },
    'dp.eparity': { en: 'Live ↔ backtest parity', es: 'Paridad en vivo ↔ backtest', zh: '實盤 ↔ 回測一致性', pt: 'Paridade ao vivo ↔ backtest', fr: 'Parité live ↔ backtest', ar: 'تطابق المباشر ↔ الاختبار الخلفي', de: 'Live ↔ Backtest-Parität' },
    'dp.estrat': { en: 'Strategy configuration', es: 'Configuración de estrategia', zh: '策略設定', pt: 'Configuração da estratégia', fr: 'Configuration de la stratégie', ar: 'إعداد الاستراتيجية', de: 'Strategiekonfiguration' },
    'dp.aprof': { en: 'Profile', es: 'Perfil', zh: '個人資料', pt: 'Perfil', fr: 'Profil', ar: 'الملف الشخصي', de: 'Profil' },
    'dp.aplan': { en: 'Membership', es: 'Membresía', zh: '會員資格', pt: 'Assinatura', fr: 'Abonnement', ar: 'العضوية', de: 'Mitgliedschaft' },
    'dp.atg': { en: 'Telegram link', es: 'Vincular Telegram', zh: 'Telegram 連結', pt: 'Vincular Telegram', fr: 'Lien Telegram', ar: 'ربط تيليجرام', de: 'Telegram-Verknüpfung' },
    'dp.awallet': { en: 'Wallet link', es: 'Vincular wallet', zh: '錢包連結', pt: 'Vincular carteira', fr: 'Lien portefeuille', ar: 'ربط المحفظة', de: 'Wallet-Verknüpfung' },
    'dp.apush': { en: 'Push notifications', es: 'Notificaciones push', zh: '推播通知', pt: 'Notificações push', fr: 'Notifications push', ar: 'الإشعارات الفورية', de: 'Push-Benachrichtigungen' },
    'dp.ainvite': { en: 'Invite friends', es: 'Invita a tus amigos', zh: '邀請朋友', pt: 'Convide amigos', fr: 'Inviter des amis', ar: 'ادعُ أصدقاءك', de: 'Freunde einladen' },
    'dp.akeys': { en: 'Exchange keys', es: 'Claves de exchange', zh: '交易所金鑰', pt: 'Chaves de exchange', fr: 'Clés d\'exchange', ar: 'مفاتيح المنصة', de: 'Exchange-Keys' },
    'dp.actl': { en: 'Live controls', es: 'Controles en vivo', zh: '實盤控制', pt: 'Controles ao vivo', fr: 'Contrôles live', ar: 'أدوات التحكم المباشر', de: 'Live-Steuerung' },
    'dp.ayield': { en: 'Yield radar', es: 'Radar de rendimiento', zh: '收益雷達', pt: 'Radar de rendimento', fr: 'Radar de rendement', ar: 'رادار العوائد', de: 'Rendite-Radar' },
    'dp.newsdd': { en: 'On your positions', es: 'Sobre tus posiciones', zh: '關於你的持倉', pt: 'Sobre suas posições', fr: 'Sur vos positions', ar: 'بخصوص مراكزك', de: 'Zu deinen Positionen' },
    'dp.newsshare': { en: 'Share with your agent', es: 'Comparte con tu agente', zh: '分享給你的代理', pt: 'Compartilhe com seu agente', fr: 'Partager avec votre agent', ar: 'شارك مع وكيلك', de: 'Mit deinem Agenten teilen' },
    'dp.newsfeed': { en: 'Latest headlines', es: 'Últimos titulares', zh: '最新頭條', pt: 'Últimas manchetes', fr: 'Derniers titres', ar: 'آخر العناوين', de: 'Neueste Schlagzeilen' },
    'dp.lbjoin': { en: 'Your spot', es: 'Tu puesto', zh: '你的名次', pt: 'Sua posição', fr: 'Votre place', ar: 'مركزك', de: 'Dein Platz' },
    'dp.lbtable': { en: 'Top traders', es: 'Mejores traders', zh: '頂尖交易者', pt: 'Melhores traders', fr: 'Meilleurs traders', ar: 'أفضل المتداولين', de: 'Top-Trader' },
    'dp.labform': { en: 'Configure a run', es: 'Configura una ejecución', zh: '設定一次執行', pt: 'Configure uma execução', fr: 'Configurer un run', ar: 'اضبط تشغيلًا', de: 'Lauf konfigurieren' },
    'dp.hubalerts': { en: 'Tripwires', es: 'Alarmas', zh: '警戒線', pt: 'Alarmes', fr: 'Alertes-pièges', ar: 'الإنذارات', de: 'Stolperdrähte' },
    'dp.hubreplay': { en: 'What-if replay', es: 'Repetición hipotética', zh: '假設情境重播', pt: 'Replay hipotético', fr: 'Rejeu hypothétique', ar: 'إعادة تشغيل افتراضية', de: 'Was-wäre-wenn-Replay' },
    'dp.hubnw': { en: 'Net worth', es: 'Patrimonio neto', zh: '淨資產', pt: 'Patrimônio líquido', fr: 'Valeur nette', ar: 'صافي الثروة', de: 'Nettovermögen' },
    'dp.hubexp': { en: 'Exposure', es: 'Exposición', zh: '曝險', pt: 'Exposição', fr: 'Exposition', ar: 'الانكشاف', de: 'Exponierung' },
    'dp.hublab': { en: 'Strategy Lab', es: 'Laboratorio de estrategias', zh: '策略實驗室', pt: 'Laboratório de estratégias', fr: 'Labo de stratégies', ar: 'مختبر الاستراتيجيات', de: 'Strategielabor' },
    'dp.hubresearch': { en: 'Research desk', es: 'Mesa de análisis', zh: '研究台', pt: 'Mesa de pesquisa', fr: 'Bureau de recherche', ar: 'مكتب البحث', de: 'Research-Desk' },
    'dp.hubtoggles': { en: 'Voice & push', es: 'Voz y push', zh: '語音與推播', pt: 'Voz e push', fr: 'Voix et push', ar: 'الصوت والإشعارات', de: 'Stimme & Push' },
    'dp.hubmcp': { en: 'Agent API (MCP)', es: 'API de agente (MCP)', zh: '代理 API（MCP）', pt: 'API do agente (MCP)', fr: 'API agent (MCP)', ar: 'واجهة الوكيل (MCP)', de: 'Agenten-API (MCP)' },
    'dp.hubllm': { en: 'Your AI engine', es: 'Tu motor de IA', zh: '你的 AI 引擎', pt: 'Seu motor de IA', fr: 'Votre moteur IA', ar: 'محرك الذكاء الاصطناعي لديك', de: 'Deine KI-Engine' },
    'dp.macro_risk': { en: 'Risk backdrop', es: 'Contexto de riesgo', zh: '風險背景', pt: 'Pano de fundo de risco', fr: 'Contexte de risque', ar: 'خلفية المخاطر', de: 'Risiko-Hintergrund' },
    'dp.readiness': { en: 'Readiness score', es: 'Puntuación de preparación', zh: '就緒評分', pt: 'Pontuação de prontidão', fr: 'Score de préparation', ar: 'درجة الجاهزية', de: 'Bereitschafts-Score' },
    'dp.incidents': { en: 'Incident ledger', es: 'Registro de incidentes', zh: '事件帳本', pt: 'Registro de incidentes', fr: 'Registre des incidents', ar: 'سجل الحوادث', de: 'Vorfall-Journal' },
    'dp.flight': { en: 'Decision ledger', es: 'Registro de decisiones', zh: '決策帳本', pt: 'Registro de decisões', fr: 'Registre des décisions', ar: 'سجل القرارات', de: 'Entscheidungs-Journal' },
    'dp.prevmkt': { en: 'The live market — real data, right now', es: 'El mercado en vivo — datos reales, ahora mismo', zh: '即時市場——此刻的真實數據', pt: 'O mercado ao vivo — dados reais, agora', fr: 'Le marché en direct — données réelles, maintenant', ar: 'السوق المباشر — بيانات حقيقية الآن', de: 'Der Live-Markt — echte Daten, genau jetzt' },
    'dp.authority': { en: 'Your trading authority', es: 'Tu autoridad de trading', zh: '你的交易授權', pt: 'Sua autoridade de trading', fr: 'Votre autorité de trading', ar: 'صلاحيتك في التداول', de: 'Deine Handelsvollmacht' },
    'dp.ticket': { en: 'Order ticket', es: 'Boleta de orden', zh: '下單單據', pt: 'Boleta de ordem', fr: 'Ticket d\'ordre', ar: 'تذكرة الأمر', de: 'Orderticket' },
    'dp.sizer': { en: 'Position sizer', es: 'Calculadora de tamaño', zh: '倉位計算器', pt: 'Calculadora de posição', fr: 'Calculateur de position', ar: 'حاسبة حجم المركز', de: 'Positionsrechner' },
    'dp.tinsight': { en: 'Decision picture', es: 'Imagen de decisión', zh: '決策圖景', pt: 'Quadro de decisão', fr: 'Tableau de décision', ar: 'صورة القرار', de: 'Entscheidungsbild' },
    'dp.prevtrack': { en: 'The engine\'s real public record', es: 'El historial público real del motor', zh: '引擎真實的公開紀錄', pt: 'O histórico público real do motor', fr: 'Le vrai historique public du moteur', ar: 'السجل العام الحقيقي للمحرك', de: 'Die echte öffentliche Bilanz der Engine' },
    'dp.venues': { en: 'Your venues — at a glance', es: 'Tus venues — de un vistazo', zh: '你的交易場所——一目了然', pt: 'Seus venues — num relance', fr: 'Vos plateformes — en un coup d\'œil', ar: 'منصاتك — بلمحة', de: 'Deine Handelsplätze — auf einen Blick' },
    'dp.lpos': { en: 'Open positions & stop-loss', es: 'Posiciones abiertas y stop-loss', zh: '持倉與止損', pt: 'Posições abertas e stop-loss', fr: 'Positions ouvertes et stop-loss', ar: 'المراكز المفتوحة ووقف الخسارة', de: 'Offene Positionen & Stop-Loss' },
    'dp.intel': { en: 'Trade intelligence', es: 'Inteligencia de trading', zh: '交易情報', pt: 'Inteligência de trading', fr: 'Intelligence de trading', ar: 'استخبارات التداول', de: 'Handels-Intelligenz' },
    'dp.networth': { en: 'Net worth — everywhere', es: 'Patrimonio neto — en todas partes', zh: '淨資產——全平台', pt: 'Patrimônio líquido — em todo lugar', fr: 'Valeur nette — partout', ar: 'صافي الثروة — في كل مكان', de: 'Nettovermögen — überall' },
    'dp.holdings': { en: 'Funds by venue & wallet', es: 'Fondos por venue y wallet', zh: '依交易所與錢包分列的資金', pt: 'Fundos por venue e carteira', fr: 'Fonds par plateforme et portefeuille', ar: 'الأموال حسب المنصة والمحفظة', de: 'Guthaben nach Börse & Wallet' },
    'dp.arena': { en: 'Paper Arena', es: 'Arena de papel', zh: '模擬競技場', pt: 'Arena de papel', fr: 'Arène papier', ar: 'حلبة التداول الورقي', de: 'Papier-Arena' },
    'dp.sentry': { en: 'Risk sentry', es: 'Centinela de riesgo', zh: '風險哨兵', pt: 'Sentinela de risco', fr: 'Sentinelle de risque', ar: 'حارس المخاطر', de: 'Risiko-Wache' },
    'dp.exposure': { en: 'Exposure — everywhere', es: 'Exposición — en todas partes', zh: '曝險——全平台', pt: 'Exposição — em todo lugar', fr: 'Exposition — partout', ar: 'الانكشاف — في كل مكان', de: 'Exponierung — überall' },
    'dp.wallet': { en: 'On-chain wallet', es: 'Wallet on-chain', zh: '鏈上錢包', pt: 'Carteira on-chain', fr: 'Portefeuille on-chain', ar: 'المحفظة على السلسلة', de: 'On-Chain-Wallet' },
    'dp.defi': { en: 'DeFi positions', es: 'Posiciones DeFi', zh: 'DeFi 部位', pt: 'Posições DeFi', fr: 'Positions DeFi', ar: 'مراكز DeFi', de: 'DeFi-Positionen' },
    'dp.idleyield': { en: 'Idle yield — best rate for idle assets', es: 'Rendimiento ocioso — la mejor tasa para activos parados', zh: '閒置收益——閒置資產的最佳利率', pt: 'Rendimento ocioso — a melhor taxa para ativos parados', fr: 'Rendement dormant — le meilleur taux pour les actifs inactifs', ar: 'عائد الأصول الخاملة — أفضل معدل للأصول غير المستخدمة', de: 'Leerlaufrendite — bester Zins für ungenutzte Assets' },
    'dp.crossyield': { en: 'Worth moving? — cross-chain yield planner', es: '¿Vale la pena mover? — planificador de rendimiento entre cadenas', zh: '值得搬家嗎？——跨鏈收益規劃', pt: 'Vale a pena mover? — planejador de rendimento entre cadeias', fr: 'Vaut-il de déplacer ? — planificateur de rendement cross-chain', ar: 'هل يستحق النقل؟ — مخطط العوائد عبر السلاسل', de: 'Lohnt ein Umzug? — Cross-Chain-Renditeplaner' },
    'dp.mystrat': { en: 'Build your own strategy', es: 'Crea tu propia estrategia', zh: '打造你自己的策略', pt: 'Crie sua própria estratégia', fr: 'Créez votre propre stratégie', ar: 'ابنِ استراتيجيتك', de: 'Baue deine eigene Strategie' },
    'dp.agents': { en: 'The lineup', es: 'La alineación', zh: '陣容', pt: 'A escalação', fr: 'L\'alignement', ar: 'التشكيلة', de: 'Die Aufstellung' },
    'dp.hubask': { en: 'Ask in one tap', es: 'Pregunta con un toque', zh: '一鍵提問', pt: 'Pergunte com um toque', fr: 'Demandez en un geste', ar: 'اسأل بنقرة', de: 'Mit einem Tipp fragen' },
    'dp.prevticket': { en: "The order ticket you'd be using", es: 'La boleta de orden que usarías', zh: '你將會使用的下單單據', pt: 'A boleta de ordem que você usaria', fr: "Le ticket d'ordre que vous utiliseriez", ar: 'تذكرة الأمر التي ستستخدمها', de: 'Das Orderticket, das du nutzen würdest' },
    'dp.unlocks': { en: 'What your account unlocks', es: 'Lo que desbloquea tu cuenta', zh: '你的帳戶解鎖了什麼', pt: 'O que sua conta desbloqueia', fr: 'Ce que votre compte débloque', ar: 'ما الذي يفتحه حسابك', de: 'Was dein Konto freischaltet' },
    // Dashboard DYNAMIC strings (dd.*) — empty states, errors, CTA labels
    'dd.err_bridge': { en: 'Analysis bridge unreachable — retry in a moment.', es: 'Puente de análisis inaccesible — reinténtalo en un momento.', zh: '分析橋接無法連線——請稍後重試。', pt: 'Ponte de análise inacessível — tente novamente em instantes.', fr: 'Pont d\'analyse injoignable — réessayez dans un instant.', ar: 'تعذّر الوصول إلى جسر التحليل — أعد المحاولة بعد لحظات.', de: 'Analyse-Bridge nicht erreichbar — gleich erneut versuchen.' },
    'dd.err_market': { en: 'Market data unavailable — retry in a moment.', es: 'Datos de mercado no disponibles — reinténtalo en un momento.', zh: '市場數據暫時無法取得——請稍後重試。', pt: 'Dados de mercado indisponíveis — tente novamente em instantes.', fr: 'Données de marché indisponibles — réessayez dans un instant.', ar: 'بيانات السوق غير متاحة — أعد المحاولة بعد لحظات.', de: 'Marktdaten nicht verfügbar — gleich erneut versuchen.' },
    'dd.err_lab': { en: 'Strategy Lab unavailable.', es: 'Laboratorio de estrategias no disponible.', zh: '策略實驗室無法使用。', pt: 'Laboratório de estratégias indisponível.', fr: 'Labo de stratégies indisponible.', ar: 'مختبر الاستراتيجيات غير متاح.', de: 'Strategielabor nicht verfügbar.' },
    'dd.err_agents': { en: 'The agent catalogue is unavailable right now.', es: 'El catálogo de agentes no está disponible ahora.', zh: '代理目錄目前無法取得。', pt: 'O catálogo de agentes está indisponível agora.', fr: 'Le catalogue d\'agents est indisponible pour le moment.', ar: 'دليل الوكلاء غير متاح حاليًا.', de: 'Der Agenten-Katalog ist derzeit nicht verfügbar.' },
    'dd.cta_tg': { en: 'Link Telegram', es: 'Vincular Telegram', zh: '連結 Telegram', pt: 'Vincular Telegram', fr: 'Lier Telegram', ar: 'اربط تيليجرام', de: 'Telegram verknüpfen' },
    'dd.cta_login': { en: 'Log in or create an account', es: 'Inicia sesión o crea una cuenta', zh: '登入或建立帳戶', pt: 'Entre ou crie uma conta', fr: 'Connectez-vous ou créez un compte', ar: 'سجّل الدخول أو أنشئ حسابًا', de: 'Anmelden oder Konto erstellen' },
    'dd.cta_ticket': { en: 'Open the trade ticket', es: 'Abrir la boleta de orden', zh: '開啟下單單據', pt: 'Abrir a boleta de ordem', fr: 'Ouvrir le ticket d\'ordre', ar: 'افتح تذكرة الأمر', de: 'Orderticket öffnen' },
    'dd.cta_paper': { en: 'Place a paper trade', es: 'Haz una operación en papel', zh: '進行一筆模擬交易', pt: 'Faça uma operação simulada', fr: 'Passer un trade papier', ar: 'نفّذ صفقة ورقية', de: 'Spielgeld-Trade platzieren' },
    'dd.cta_verify': { en: 'Resend verification', es: 'Reenviar verificación', zh: '重新發送驗證', pt: 'Reenviar verificação', fr: 'Renvoyer la vérification', ar: 'أعد إرسال التحقق', de: 'Bestätigung erneut senden' },
    'dd.cta_live': { en: 'Review live controls', es: 'Revisar controles en vivo', zh: '檢視實盤控制', pt: 'Revisar controles ao vivo', fr: 'Vérifier les contrôles live', ar: 'راجع أدوات التحكم المباشر', de: 'Live-Steuerung prüfen' },
    'dd.e_portfolio': { en: 'No portfolio data yet.', es: 'Aún no hay datos de cartera.', zh: '尚無投資組合數據。', pt: 'Ainda não há dados de portfólio.', fr: 'Pas encore de données de portefeuille.', ar: 'لا توجد بيانات محفظة بعد.', de: 'Noch keine Portfoliodaten.' },
    'dd.e_positions': { en: 'No open positions. The Trade view has a full order ticket.', es: 'Sin posiciones abiertas. La vista Trade tiene una boleta completa.', zh: '目前沒有持倉。交易頁面有完整的下單單據。', pt: 'Sem posições abertas. A view Trade tem uma boleta completa.', fr: 'Aucune position ouverte. La vue Trade contient un ticket complet.', ar: 'لا توجد مراكز مفتوحة. تحتوي صفحة التداول على تذكرة أمر كاملة.', de: 'Keine offenen Positionen. Im Trade-Bereich findest du ein vollständiges Orderticket.' },
    'dd.e_signals': { en: 'No signals yet — they appear as the engine scans.', es: 'Aún no hay señales — aparecen mientras el motor escanea.', zh: '尚無訊號——引擎掃描時會出現。', pt: 'Ainda sem sinais — aparecem conforme o motor varre.', fr: 'Pas encore de signaux — ils apparaissent au fil des scans.', ar: 'لا إشارات بعد — تظهر أثناء مسح المحرك.', de: 'Noch keine Signale — sie erscheinen, während die Engine scannt.' },
    'dd.e_macro': { en: 'The macro backdrop appears once market data is available.', es: 'El contexto macro aparece cuando hay datos de mercado.', zh: '市場數據可用後即顯示宏觀背景。', pt: 'O pano de fundo macro aparece quando houver dados de mercado.', fr: 'Le contexte macro apparaît dès que les données de marché arrivent.', ar: 'تظهر الخلفية الكلية عند توفر بيانات السوق.', de: 'Der Makro-Hintergrund erscheint, sobald Marktdaten verfügbar sind.' },
    'dd.e_agent': { en: 'Agent status unavailable right now.', es: 'Estado del agente no disponible ahora.', zh: '代理狀態目前無法取得。', pt: 'Status do agente indisponível agora.', fr: 'Statut de l\'agent indisponible pour le moment.', ar: 'حالة الوكيل غير متاحة حاليًا.', de: 'Agentenstatus derzeit nicht verfügbar.' },
    'dd.e_letter': { en: 'The first letter writes itself after the first full week of recorded activity.', es: 'La primera carta se escribe sola tras la primera semana completa de actividad registrada.', zh: '第一封信會在完整記錄一週活動後自動生成。', pt: 'A primeira carta se escreve após a primeira semana completa de atividade registrada.', fr: 'La première lettre s\'écrit après une première semaine complète d\'activité enregistrée.', ar: 'تُكتب الرسالة الأولى بعد أول أسبوع كامل من النشاط المسجَّل.', de: 'Der erste Brief schreibt sich nach der ersten vollen Woche aufgezeichneter Aktivität.' },
    // Dashboard panel titles & sublabels (dp.*) — applied at view mount and,
    // for async panels, when renderPanel lands content.
    'dp.tripwires': { en: 'My tripwires', es: 'Mis alarmas', zh: '我的警戒線', pt: 'Meus alarmes', fr: 'Mes alertes-pièges', ar: 'إنذاراتي', de: 'Meine Stolperdrähte' },
    'dp.next': { en: 'Getting started', es: 'Primeros pasos', zh: '快速上手', pt: 'Primeiros passos', fr: 'Bien démarrer', ar: 'البداية', de: 'Erste Schritte' },
    'dp.watch': { en: 'Watchlist', es: 'Lista de seguimiento', zh: '自選清單', pt: 'Lista de acompanhamento', fr: 'Liste de suivi', ar: 'قائمة المراقبة', de: 'Beobachtungsliste' },
    'dp.watch_sub': { en: 'engine patterns push for these', es: 'el motor te avisa de patrones en estos', zh: '引擎為這些推送形態提醒', pt: 'o motor avisa padrões destes', fr: 'le moteur pousse les patterns de ceux-ci', ar: 'يرسل المحرك أنماط هذه الرموز', de: 'die Engine meldet Formationen dazu' },
    'dp.agent': { en: 'Your agent', es: 'Tu agente', zh: '你的代理', pt: 'Seu agente', fr: 'Votre agent', ar: 'وكيلك', de: 'Dein Agent' },
    'dp.agent_sub': { en: "what it's doing for you", es: 'qué está haciendo por ti', zh: '它正在為你做什麼', pt: 'o que ele está fazendo por você', fr: 'ce qu’il fait pour vous', ar: 'ما الذي يفعله لأجلك', de: 'was er für dich tut' },
    'dp.mind': { en: 'Agent mind-stream', es: 'Mente del agente en vivo', zh: '代理思維流', pt: 'Fluxo mental do agente', fr: 'Flux de pensée de l’agent', ar: 'تيار أفكار الوكيل', de: 'Gedankenstrom des Agenten' },
    'dp.mind_link': { en: 'full feed →', es: 'feed completo →', zh: '完整動態 →', pt: 'feed completo →', fr: 'flux complet →', ar: 'الموجز الكامل ←', de: 'kompletter Feed →' },
    'dp.verify': { en: "Don't trust the dashboard — verify the fills", es: 'No confíes en el panel — verifica las ejecuciones', zh: '別只信儀表板——驗證成交', pt: 'Não confie no painel — verifique as execuções', fr: 'Ne croyez pas le tableau de bord — vérifiez les exécutions', ar: 'لا تثق باللوحة — تحقق من التنفيذات', de: 'Vertrau dem Dashboard nicht — überprüfe die Ausführungen' },
    'dp.verify_link': { en: 'Proof of PnL →', es: 'Prueba de PnL →', zh: 'PnL 證明 →', pt: 'Prova de PnL →', fr: 'Preuve de PnL →', ar: 'إثبات الأرباح ←', de: 'PnL-Nachweis →' },
    'dp.verify_p': { en: 'Every figure here is reconstructed from raw exchange fills and published as a sealed, hash-verifiable statement. Re-derive the hash in your own browser — no login, no trust required.', es: 'Cada cifra se reconstruye desde las ejecuciones brutas del exchange y se publica como un estado sellado verificable por hash. Recalcula el hash en tu propio navegador — sin login y sin confianza requerida.', zh: '這裡的每個數字都由交易所原始成交重建，並以密封、可驗證雜湊的報表發布。在你自己的瀏覽器中重新計算雜湊——無需登入、無需信任。', pt: 'Cada número aqui é reconstruído das execuções brutas da exchange e publicado como um extrato selado verificável por hash. Recalcule o hash no seu navegador — sem login, sem confiança exigida.', fr: 'Chaque chiffre est reconstruit à partir des exécutions brutes de l’exchange et publié comme un relevé scellé vérifiable par hash. Recalculez le hash dans votre navigateur — sans login, sans confiance requise.', ar: 'كل رقم هنا يُعاد بناؤه من تنفيذات المنصة الخام ويُنشر كبيان مختوم يمكن التحقق من تجزئته. أعد اشتقاق التجزئة في متصفحك — دون تسجيل دخول ودون حاجة للثقة.', de: 'Jede Zahl hier wird aus rohen Börsenausführungen rekonstruiert und als versiegelte, per Hash überprüfbare Abrechnung veröffentlicht. Berechne den Hash in deinem eigenen Browser neu — ohne Login, ohne Vertrauensvorschuss.' },
    'dp.verify_btn': { en: '🔐 Re-verify the fills', es: '🔐 Reverificar las ejecuciones', zh: '🔐 重新驗證成交', pt: '🔐 Reverificar as execuções', fr: '🔐 Revérifier les exécutions', ar: '🔐 أعد التحقق من التنفيذات', de: '🔐 Ausführungen erneut prüfen' },
    'dp.track_btn': { en: '📈 Public track record', es: '📈 Historial público', zh: '📈 公開績效紀錄', pt: '📈 Histórico público', fr: '📈 Historique public', ar: '📈 السجل العام', de: '📈 Öffentliche Erfolgsbilanz' },
    'dp.macro': { en: 'Macro backdrop', es: 'Contexto macro', zh: '宏觀背景', pt: 'Pano de fundo macro', fr: 'Contexte macro', ar: 'الخلفية الكلية', de: 'Makro-Hintergrund' },
    'dp.macro_link': { en: 'open Macro →', es: 'abrir Macro →', zh: '打開宏觀 →', pt: 'abrir Macro →', fr: 'ouvrir Macro →', ar: 'افتح الماكرو ←', de: 'Makro öffnen →' },
    'dp.letter': { en: 'The Agent Letter', es: 'La Carta del Agente', zh: '代理週報', pt: 'A Carta do Agente', fr: 'La Lettre de l’Agent', ar: 'رسالة الوكيل', de: 'Der Agenten-Brief' },
    'dp.letter_link': { en: 'public archive →', es: 'archivo público →', zh: '公開存檔 →', pt: 'arquivo público →', fr: 'archives publiques →', ar: 'الأرشيف العام ←', de: 'öffentliches Archiv →' },
    'dp.hpos': { en: 'Open positions', es: 'Posiciones abiertas', zh: '持倉中', pt: 'Posições abertas', fr: 'Positions ouvertes', ar: 'المراكز المفتوحة', de: 'Offene Positionen' },
    'dp.hsig': { en: 'Latest engine signals', es: 'Últimas señales del motor', zh: '引擎最新訊號', pt: 'Últimos sinais do motor', fr: 'Derniers signaux du moteur', ar: 'أحدث إشارات المحرك', de: 'Neueste Engine-Signale' },
    'dp.away': { en: 'While you were away', es: 'Mientras no estabas', zh: '你不在的時候', pt: 'Enquanto você esteve fora', fr: 'Pendant votre absence', ar: 'أثناء غيابك', de: 'Während du weg warst' },
    'dp.away_gone': { en: 'gone', es: 'ausente', zh: '離開', pt: 'ausente', fr: 'absent', ar: 'غبت', de: 'weg' },
    'dp.caught_up': { en: 'Caught up ✓', es: 'Al día ✓', zh: '已看完 ✓', pt: 'Em dia ✓', fr: 'À jour ✓', ar: 'اطلعت ✓', de: 'Auf dem Laufenden ✓' },
    'dp.chart': { en: 'Price chart', es: 'Gráfico de precio', zh: '價格圖表', pt: 'Gráfico de preço', fr: 'Graphique de prix', ar: 'مخطط السعر', de: 'Preischart' },
    'dp.insight': { en: 'AI decision picture', es: 'Imagen de decisión de la IA', zh: 'AI 決策圖景', pt: 'Quadro de decisão da IA', fr: 'Tableau de décision de l’IA', ar: 'صورة قرار الذكاء الاصطناعي', de: 'KI-Entscheidungsbild' },
    'dp.insight_sub': { en: 'the same read the engine trades off', es: 'la misma lectura con la que opera el motor', zh: '與引擎交易所依據的同一解讀', pt: 'a mesma leitura com que o motor opera', fr: 'la même lecture sur laquelle trade le moteur', ar: 'نفس القراءة التي يتداول بها المحرك', de: 'dieselbe Lesart, nach der die Engine handelt' },
    'dp.depth': { en: 'Order book', es: 'Libro de órdenes', zh: '訂單簿', pt: 'Livro de ofertas', fr: 'Carnet d’ordres', ar: 'دفتر الأوامر', de: 'Orderbuch' },
    'dp.funding': { en: 'Funding rate', es: 'Tasa de funding', zh: '資金費率', pt: 'Taxa de funding', fr: 'Taux de funding', ar: 'معدل التمويل', de: 'Funding-Rate' },
    'dp.xfunding': { en: 'Cross-venue funding', es: 'Funding entre venues', zh: '跨交易所資金費率', pt: 'Funding entre venues', fr: 'Funding multi-plateformes', ar: 'التمويل عبر المنصات', de: 'Funding über Börsen hinweg' },
    'dp.arb': { en: 'Funding-arb paper tracker', es: 'Rastreador (papel) de arbitraje de funding', zh: '資金費率套利模擬追蹤', pt: 'Rastreador (papel) de arbitragem de funding', fr: 'Suivi (papier) d’arbitrage de funding', ar: 'متتبع تحكيم التمويل (ورقي)', de: 'Funding-Arb-Papiertracker' },
    'dp.dex': { en: 'DEX ↔ CEX — Hyperliquid vs this venue', es: 'DEX ↔ CEX — Hyperliquid vs este venue', zh: 'DEX ↔ CEX — Hyperliquid 對比本所', pt: 'DEX ↔ CEX — Hyperliquid vs este venue', fr: 'DEX ↔ CEX — Hyperliquid vs cette plateforme', ar: 'DEX ↔ CEX — Hyperliquid مقابل هذه المنصة', de: 'DEX ↔ CEX — Hyperliquid vs. diese Börse' },
    'hero.install': { en: 'Install the app', es: 'Instalar la app', zh: '安裝應用', pt: 'Instalar a app', fr: 'Installer l\'app', ar: 'ثبّت التطبيق', de: 'App installieren' },
    'hero.explore_arena': { en: 'Paper Arena', es: 'Arena de práctica', zh: '模擬交易競技場', pt: 'Arena de simulação', fr: 'Arène papier', ar: 'ساحة التداول الورقي', de: 'Papier-Arena' },
    'sec.arena_h': { en: 'Prove your edge before you risk a cent', es: 'Demuestra tu ventaja antes de arriesgar un centavo', zh: '在冒任何風險之前證明你的實力', pt: 'Prove a sua vantagem antes de arriscar um centavo', fr: 'Prouvez votre avantage avant de risquer un centime', ar: 'أثبت مهارتك قبل أن تخاطر بأي شيء', de: 'Beweise deinen Vorsprung, bevor du einen Cent riskierst' },
    'sec.arena_p': { en: 'Every account gets a paper trading account the moment you open the Arena — the same virtual starting stake for everyone, no exchange keys, no setup. Trade live markets at real prices with real liquidation mechanics, and climb a public leaderboard ranked purely on percent return. When paper competitions run, this is where they happen.', es: 'Cada cuenta recibe una cuenta de práctica al abrir la Arena: el mismo capital virtual inicial para todos, sin claves de exchange, sin configuración. Opera mercados en vivo a precios reales con mecánica de liquidación real y sube en una clasificación pública ordenada solo por retorno porcentual. Cuando haya competiciones, será aquí.', zh: '打開競技場的那一刻，每個帳戶都會獲得一個模擬交易帳戶——人人相同的虛擬起始資金，無需交易所密鑰，無需設定。以真實價格交易實時市場，體驗真實的強平機制，並在僅按回報百分比排名的公開排行榜上攀升。模擬交易比賽將在這裡舉行。', pt: 'Cada conta recebe uma conta de simulação no momento em que abre a Arena — o mesmo capital virtual inicial para todos, sem chaves de exchange, sem configuração. Negocie mercados ao vivo a preços reais com mecânica de liquidação real e suba num ranking público ordenado apenas pelo retorno percentual. Quando houver competições, será aqui.', fr: 'Chaque compte reçoit un compte de trading papier dès l\'ouverture de l\'Arène — la même mise virtuelle de départ pour tous, sans clés d\'exchange, sans configuration. Tradez les marchés en direct aux prix réels avec de vrais mécanismes de liquidation, et grimpez un classement public fondé uniquement sur le rendement en pourcentage. Les compétitions papier se joueront ici.', ar: 'يحصل كل حساب على حساب تداول ورقي لحظة فتح الساحة — نفس رأس المال الافتراضي للجميع، بلا مفاتيح منصات، بلا إعداد. تداول الأسواق الحية بأسعار حقيقية وآليات تصفية حقيقية، وتسلَّق لوحة صدارة عامة تُرتَّب حسب نسبة العائد فقط. وعندما تُقام المسابقات الورقية، فهنا مكانها.', de: 'Jedes Konto erhält ein Papierhandelskonto, sobald du die Arena öffnest — für alle derselbe virtuelle Starteinsatz, keine Exchange-Keys, keine Einrichtung.' },
    'sec.arena_cta': { en: 'Enter the Arena →', es: 'Entrar en la Arena →', zh: '進入競技場 →', pt: 'Entrar na Arena →', fr: 'Entrer dans l\'Arène →', ar: 'ادخل الساحة →', de: 'Die Arena betreten →' },
    'sec.arena_f1h': { en: 'Same stake, pure skill', es: 'Mismo capital, pura habilidad', zh: '同樣起點，純粹實力', pt: 'Mesmo capital, pura habilidade', fr: 'Même mise, pur talent', ar: 'نفس رأس المال، مهارة خالصة', de: 'Gleicher Einsatz, reines Können' },
    'sec.arena_f1p': { en: 'Everyone starts identical, so the ranking measures nothing but your calls — percent return, anonymous handles, zero dollar-waving.', es: 'Todos empiezan igual, así que la clasificación solo mide tus decisiones: retorno porcentual, alias anónimos, cero presunción.', zh: '人人起點相同，排名只衡量你的判斷——回報百分比、匿名代號、不炫耀金額。', pt: 'Todos começam iguais, por isso o ranking mede apenas as suas decisões — retorno percentual, alias anónimos, zero ostentação.', fr: 'Tout le monde part à égalité : le classement ne mesure que vos décisions — rendement en pourcentage, pseudonymes anonymes, zéro esbroufe.', ar: 'يبدأ الجميع سواسية، فالترتيب لا يقيس إلا قراراتك — نسبة العائد، أسماء مستعارة، بلا تباهٍ بالمبالغ.', de: 'Alle starten identisch, also misst die Rangliste nichts als deine Calls — Prozentrendite, anonyme Namen, kein Geldprotzen.' },
    'sec.arena_f2h': { en: 'Real prices, real mechanics', es: 'Precios reales, mecánica real', zh: '真實價格，真實機制', pt: 'Preços reais, mecânica real', fr: 'Vrais prix, vraie mécanique', ar: 'أسعار حقيقية وآليات حقيقية', de: 'Echte Preise, echte Mechanik' },
    'sec.arena_f2p': { en: 'Fills at live market prices, leverage up to 20×, and liquidations enforced with the same math the Stress Lab models — honest practice, not a toy.', es: 'Ejecución a precios de mercado en vivo, apalancamiento hasta 20× y liquidaciones con la misma matemática del Stress Lab: práctica honesta, no un juguete.', zh: '按實時市價成交，槓桿最高 20×，強平採用與壓力實驗室相同的數學——誠實的練習，不是玩具。', pt: 'Execução a preços de mercado ao vivo, alavancagem até 20× e liquidações com a mesma matemática do Stress Lab — prática honesta, não um brinquedo.', fr: 'Exécution aux prix du marché en direct, levier jusqu\'à 20×, liquidations calculées avec les mêmes maths que le Stress Lab — un entraînement honnête, pas un jouet.', ar: 'تنفيذ بأسعار السوق الحية، رافعة حتى ‎20×، وتصفية بنفس رياضيات مختبر الضغط — تدريب صادق، لا لعبة.', de: 'Ausführungen zu Live-Marktpreisen, Hebel bis 20× und Liquidationen mit derselben Mathematik, die das Stresslabor modelliert — ehrliches Training, kein Spielzeug.' },
    'sec.arena_f3h': { en: 'Zero friction', es: 'Cero fricción', zh: '零門檻', pt: 'Zero fricção', fr: 'Zéro friction', ar: 'بلا أي عوائق', de: 'Null Reibung' },
    'sec.arena_f3p': { en: 'No API keys, no deposit, nothing to configure — register and your account exists. The safest possible way to meet the platform.', es: 'Sin claves API, sin depósito, nada que configurar: regístrate y tu cuenta existe. La forma más segura de conocer la plataforma.', zh: '無需 API 密鑰、無需入金、無需設定——註冊即擁有帳戶。認識這個平台最安全的方式。', pt: 'Sem chaves API, sem depósito, nada para configurar — registe-se e a sua conta existe. A forma mais segura de conhecer a plataforma.', fr: 'Pas de clés API, pas de dépôt, rien à configurer — inscrivez-vous et votre compte existe. La façon la plus sûre de découvrir la plateforme.', ar: 'بلا مفاتيح API، بلا إيداع، بلا إعداد — سجِّل وسيكون حسابك جاهزًا. أسلم طريقة للتعرف على المنصة.', de: 'Keine API-Keys, keine Einzahlung, nichts zu konfigurieren — registrieren und dein Konto existiert. Der sicherste Weg, die Plattform kennenzulernen.' },
    'hero.explore_map': { en: '3D Strength Map', es: 'Mapa de fuerza 3D', zh: '3D 強度地圖', pt: 'Mapa de força 3D', fr: 'Carte de force 3D', ar: 'خريطة القوة ثلاثية الأبعاد', de: '3D-Stärkekarte' },
    'hero.explore_guardian': { en: 'Guardian safety suite', es: 'Suite de seguridad Guardian', zh: 'Guardian 安全套件', pt: 'Suíte de segurança Guardian', fr: 'Suite de sécurité Guardian', ar: 'مجموعة أمان Guardian', de: 'Guardian-Sicherheitspaket' },
    'hero.explore_market': { en: 'Marketplace', es: 'Mercado', zh: '市場', pt: 'Mercado', fr: 'Place de marché', ar: 'السوق', de: 'Marktplatz' },
    'sec.ask_h': { en: 'Talk to it, right here', es: 'Habla con él, aquí mismo', zh: '就在這裡與它對話', pt: 'Fale com ele, aqui mesmo', fr: 'Parlez-lui, ici même', ar: 'تحدّث معه، هنا مباشرة', de: 'Sprich direkt hier mit ihr' },
    'sec.ask_p': { en: 'This is the live analyst — the same one behind the Telegram bot. Ask it anything; no account needed.', es: 'Este es el analista en vivo, el mismo que está detrás del bot de Telegram. Pregúntale lo que quieras; no necesitas cuenta.', zh: '這是實時分析師——與 Telegram 機器人背後的是同一個。隨便問；無需帳戶。', pt: 'Este é o analista ao vivo — o mesmo por trás do bot do Telegram. Pergunte o que quiser; sem precisar de conta.', fr: 'Voici l’analyste en direct — le même que derrière le bot Telegram. Posez-lui n’importe quelle question ; aucun compte requis.', ar: 'هذا هو المحلّل المباشر — نفسه الذي يشغّل بوت تيليجرام. اسأله أي شيء؛ دون حاجة إلى حساب.', de: 'Das ist der Live-Analyst — derselbe wie hinter dem Telegram-Bot. Frag ihn alles; kein Konto nötig.' },
    'sec.live_h': { en: 'Watch the agent think — live', es: 'Observa al agente pensar — en vivo', zh: '即時觀看智能體思考', pt: 'Veja o agente pensar — ao vivo', fr: 'Regardez l’agent réfléchir — en direct', ar: 'شاهد الوكيل يفكّر — مباشرة', de: 'Sieh dem Agenten beim Denken zu — live' },
    'sec.live_p': { en: 'The real engine’s mind-stream: scans, trade theses, executions and stop moves, streamed straight from the bot. No mockups.', es: 'El flujo mental del motor real: análisis, tesis de operación, ejecuciones y movimientos de stop, transmitidos directamente desde el bot. Sin maquetas.', zh: '真實引擎的思緒流：掃描、交易論點、成交與停損調整，直接由機器人串流。絕無虛構。', pt: 'O fluxo de pensamento do motor real: varreduras, teses de operação, execuções e ajustes de stop, transmitidos direto do bot. Sem simulações.', fr: 'Le flux de pensée du vrai moteur : analyses, thèses de trade, exécutions et déplacements de stop, diffusés directement depuis le bot. Aucune maquette.', ar: 'تدفّق تفكير المحرك الحقيقي: عمليات المسح وأطروحات الصفقات والتنفيذ وتحريك أوامر الإيقاف، تُبثّ مباشرة من البوت. بلا نماذج وهمية.', de: 'Der Gedankenstrom der echten Engine: Scans, Handelsthesen, Ausführungen und Stopp-Anpassungen, direkt vom Bot gestreamt. Keine Attrappen.' },

    'auth.tab_create': { en: 'Create account', es: 'Crear cuenta', zh: '建立帳戶', pt: 'Criar conta', fr: 'Créer un compte', ar: 'إنشاء حساب', de: 'Konto erstellen' },
    'auth.tab_login': { en: 'Log in', es: 'Iniciar sesión', zh: '登入', pt: 'Entrar', fr: 'Se connecter', ar: 'تسجيل الدخول', de: 'Anmelden' },
    'auth.confirm_pass': { en: 'Confirm password', es: 'Confirmar contraseña', zh: '確認密碼', pt: 'Confirmar senha', fr: 'Confirmer le mot de passe', ar: 'تأكيد كلمة المرور', de: 'Passwort bestätigen' },
    'auth.confirm_ph': { en: 'Repeat password', es: 'Repite la contraseña', zh: '重複輸入密碼', pt: 'Repita a senha', fr: 'Répétez le mot de passe', ar: 'أعد إدخال كلمة المرور', de: 'Passwort wiederholen' },
    'auth.paper_note': { en: 'Paper trading works immediately — no Telegram, no exchange keys.', es: 'El trading en simulación funciona de inmediato: sin Telegram, sin claves de exchange.', zh: '模擬交易即刻可用——無需 Telegram，無需交易所金鑰。', pt: 'O paper trading funciona imediatamente — sem Telegram, sem chaves de exchange.', fr: 'Le trading en simulation fonctionne immédiatement — sans Telegram, sans clés d’exchange.', ar: 'يعمل التداول التجريبي فورًا — دون تيليجرام ودون مفاتيح منصّة.', de: 'Papierhandel funktioniert sofort — ohne Telegram, ohne Exchange-Keys.' },
    'auth.email': { en: 'Email', es: 'Correo', zh: '電子郵件', pt: 'E-mail', fr: 'E-mail', ar: 'البريد الإلكتروني', de: 'E-Mail' },
    'auth.password': { en: 'Password', es: 'Contraseña', zh: '密碼', pt: 'Senha', fr: 'Mot de passe', ar: 'كلمة المرور', de: 'Passwort' },
    'auth.email_ph': { en: 'you@example.com', es: 'tu@ejemplo.com', zh: 'you@example.com', pt: 'voce@exemplo.com', fr: 'vous@exemple.com', ar: 'you@example.com', de: 'du@beispiel.de' },
    'auth.pass_min_ph': { en: 'Min 10 characters', es: 'Mínimo 10 caracteres', zh: '至少 10 個字元', pt: 'Mínimo 10 caracteres', fr: 'Minimum 10 caractères', ar: '10 أحرف على الأقل', de: 'Mindestens 10 Zeichen' },
    'auth.pass_ph': { en: 'Your password', es: 'Tu contraseña', zh: '你的密碼', pt: 'Sua senha', fr: 'Votre mot de passe', ar: 'كلمة المرور الخاصة بك', de: 'Dein Passwort' },
    'auth.create': { en: 'Create free account', es: 'Crear cuenta gratis', zh: '建立免費帳戶', pt: 'Criar conta grátis', fr: 'Créer un compte gratuit', ar: 'أنشئ حسابًا مجانيًا', de: 'Kostenloses Konto erstellen' },
    'auth.or_continue': { en: 'or continue with', es: 'o continúa con', zh: '或使用以下方式繼續', pt: 'ou continue com', fr: 'ou continuer avec', ar: 'أو تابع باستخدام', de: 'oder weiter mit' },
    'auth.continue_wallet': { en: 'Continue with a wallet', es: 'Continuar con una wallet', zh: '使用錢包繼續', pt: 'Continuar com uma carteira', fr: 'Continuer avec un portefeuille', ar: 'المتابعة بمحفظة', de: 'Mit einer Wallet fortfahren' },
    'auth.no_wallet': { en: 'No wallet detected — install MetaMask or a browser wallet.', es: 'No se detectó ninguna wallet — instala MetaMask u otra wallet de navegador.', zh: '未偵測到錢包——請安裝 MetaMask 或瀏覽器錢包。', pt: 'Nenhuma carteira detectada — instale a MetaMask ou uma carteira de navegador.', fr: 'Aucun portefeuille détecté — installez MetaMask ou un portefeuille de navigateur.', ar: 'لم يتم العثور على محفظة — ثبّت MetaMask أو محفظة متصفح.', de: 'Keine Wallet erkannt — installiere MetaMask oder eine Browser-Wallet.' },
    'auth.no_wallet_phone': { en: 'No wallet detected — install MetaMask, or use "Link with phone" below.', es: 'No se detectó ninguna wallet — instala MetaMask o usa "Vincular con el teléfono" abajo.', zh: '未偵測到錢包——請安裝 MetaMask，或使用下方「以手機連結」。', pt: 'Nenhuma carteira detectada — instale a MetaMask ou use "Vincular com o telefone" abaixo.', fr: 'Aucun portefeuille détecté — installez MetaMask ou utilisez « Lier avec le téléphone » ci-dessous.', ar: 'لم يتم العثور على محفظة — ثبّت MetaMask أو استخدم "الربط عبر الهاتف" أدناه.', de: 'Keine Wallet erkannt — installiere MetaMask oder nutze unten „Mit Telefon verknüpfen“.' },
    'ref.invited_full': { en: 'Invited by <b></b> — ranked on the <a href="/leaderboard">verifiable board</a>. Your signup credits them.', es: 'Invitado por <b></b> — clasificado en el <a href="/leaderboard">tablero verificable</a>. Tu registro le da crédito.', zh: '由 <b></b> 邀請——名列<a href="/leaderboard">可驗證排行榜</a>。你的註冊將歸功於對方。', pt: 'Convidado por <b></b> — classificado no <a href="/leaderboard">quadro verificável</a>. Seu cadastro credita a ele.', fr: 'Invité par <b></b> — classé au <a href="/leaderboard">classement vérifiable</a>. Votre inscription lui est créditée.', ar: 'بدعوة من <b></b> — مصنَّف على <a href="/leaderboard">اللوحة القابلة للتحقق</a>. تسجيلك يُحتسب له.', de: 'Eingeladen von <b></b> — gelistet auf der <a href="/leaderboard">überprüfbaren Bestenliste</a>. Deine Anmeldung wird ihm gutgeschrieben.' },
    'ref.friend_note': { en: '🎁 A friend invited you — create your account to get started.', es: '🎁 Un amigo te invitó — crea tu cuenta para empezar.', zh: '🎁 朋友邀請了你——建立帳戶即可開始。', pt: '🎁 Um amigo convidou você — crie sua conta para começar.', fr: '🎁 Un ami vous a invité — créez votre compte pour commencer.', ar: '🎁 دعاك صديق — أنشئ حسابك للبدء.', de: '🎁 Ein Freund hat dich eingeladen — erstelle dein Konto, um loszulegen.' },
    'ref.friend_hero': { en: '🎁 A friend invited you — your signup credits them.', es: '🎁 Un amigo te invitó — tu registro le da crédito.', zh: '🎁 朋友邀請了你——你的註冊將歸功於對方。', pt: '🎁 Um amigo convidou você — seu cadastro credita a ele.', fr: '🎁 Un ami vous a invité — votre inscription lui est créditée.', ar: '🎁 دعاك صديق — تسجيلك يُحتسب له.', de: '🎁 Ein Freund hat dich eingeladen — deine Anmeldung wird ihm gutgeschrieben.' },
    'ref.invited_short': { en: 'Invited by <b></b> — ranked on the <a href="/leaderboard">verifiable board</a>.', es: 'Invitado por <b></b> — clasificado en el <a href="/leaderboard">tablero verificable</a>.', zh: '由 <b></b> 邀請——名列<a href="/leaderboard">可驗證排行榜</a>。', pt: 'Convidado por <b></b> — classificado no <a href="/leaderboard">quadro verificável</a>.', fr: 'Invité par <b></b> — classé au <a href="/leaderboard">classement vérifiable</a>.', ar: 'بدعوة من <b></b> — مصنَّف على <a href="/leaderboard">اللوحة القابلة للتحقق</a>.', de: 'Eingeladen von <b></b> — gelistet auf der <a href="/leaderboard">überprüfbaren Bestenliste</a>.' },

    'foot.risk': {
      en: 'Trading futures involves substantial risk of loss and is not suitable for every investor. RUNECLAW is a trading tool, not financial advice. Use withdrawal-disabled API keys and never risk more than you can afford to lose.',
      es: 'Operar con futuros conlleva un riesgo sustancial de pérdida y no es adecuado para todos los inversores. RUNECLAW es una herramienta de trading, no asesoramiento financiero. Usa claves API sin permiso de retiro y nunca arriesgues más de lo que puedas permitirte perder.',
      zh: '交易期貨涉及重大虧損風險，並非適合所有投資者。RUNECLAW 是交易工具，而非財務建議。請使用停用提款權限的 API 金鑰，切勿冒超出承受能力的風險。',
      pt: 'Operar futuros envolve risco substancial de perda e não é adequado para todos os investidores. A RUNECLAW é uma ferramenta de trading, não aconselhamento financeiro. Use chaves de API sem permissão de saque e nunca arrisque mais do que pode perder.',
      fr: 'Le trading de futures comporte un risque de perte substantiel et ne convient pas à tous les investisseurs. RUNECLAW est un outil de trading, pas un conseil financier. Utilisez des clés API sans retrait et ne risquez jamais plus que ce que vous pouvez vous permettre de perdre.',
      ar: 'ينطوي تداول العقود الآجلة على مخاطر خسارة كبيرة وقد لا يناسب كل مستثمر. RUNECLAW أداة تداول وليست نصيحة مالية. استخدم مفاتيح API مع تعطيل السحب ولا تخاطر أبدًا بأكثر مما يمكنك تحمّل خسارته.',
      de: 'Der Handel mit Futures birgt ein erhebliches Verlustrisiko und ist nicht für jeden Anleger geeignet. RUNECLAW ist ein Handelswerkzeug, keine Finanzberatung. Verwende API-Keys ohne Auszahlungsrecht und riskiere nie mehr, als du verlieren kannst.',
    },

    'lang.label': { en: 'Language', es: 'Idioma', zh: '語言', pt: 'Idioma', fr: 'Langue', ar: 'اللغة', de: 'Sprache' },

    // Dashboard shell chrome (static in dashboard.html).
    'dash.skip': { en: 'Skip to content', es: 'Saltar al contenido', zh: '跳至內容', pt: 'Pular para o conteúdo', fr: 'Aller au contenu', ar: 'تخطَّ إلى المحتوى', de: 'Zum Inhalt springen' },
    'dash.account': { en: 'Account', es: 'Cuenta', zh: '帳戶', pt: 'Conta', fr: 'Compte', ar: 'الحساب', de: 'Konto' },
    'dash.chat_ph': { en: 'Ask anything — or "buy SOL 71 sl 70 tp 76"', es: 'Pregunta lo que sea — o "buy SOL 71 sl 70 tp 76"', zh: '隨便問——或輸入「buy SOL 71 sl 70 tp 76」', pt: 'Pergunte qualquer coisa — ou "buy SOL 71 sl 70 tp 76"', fr: 'Demandez n’importe quoi — ou "buy SOL 71 sl 70 tp 76"', ar: 'اسأل أي شيء — أو اكتب "buy SOL 71 sl 70 tp 76"', de: 'Frag irgendetwas — oder „buy SOL 71 sl 70 tp 76“' },
    'dash.confirm_trade': { en: 'Confirm trade', es: 'Confirmar operación', zh: '確認交易', pt: 'Confirmar operação', fr: 'Confirmer l’ordre', ar: 'تأكيد الصفقة', de: 'Trade bestätigen' },
    'dash.confirm': { en: 'Confirm', es: 'Confirmar', zh: '確認', pt: 'Confirmar', fr: 'Confirmer', ar: 'تأكيد', de: 'Bestätigen' },
    'dash.cancel_order': { en: 'Cancel order', es: 'Cancelar orden', zh: '取消訂單', pt: 'Cancelar ordem', fr: 'Annuler l’ordre', ar: 'إلغاء الأمر', de: 'Order stornieren' },

    // Dashboard nav labels (left rail + bottom tabbar), built in dashboard.js.
    'nav.home': { en: 'Home', es: 'Inicio', zh: '首頁', pt: 'Início', fr: 'Accueil', ar: 'الرئيسية', de: 'Start' },
    'nav.chat': { en: 'AI Chat', es: 'Chat IA', zh: 'AI 聊天', pt: 'Chat IA', fr: 'Chat IA', ar: 'محادثة الذكاء', de: 'KI-Chat' },
    'nav.hub': { en: 'Agent Hub', es: 'Centro del agente', zh: '智能體中心', pt: 'Central do agente', fr: 'Hub de l’agent', ar: 'مركز الوكيل', de: 'Agenten-Hub' },
    'nav.markets': { en: 'Markets', es: 'Mercados', zh: '市場', pt: 'Mercados', fr: 'Marchés', ar: 'الأسواق', de: 'Märkte' },
    'nav.macro': { en: 'Macro', es: 'Macro', zh: '總經', pt: 'Macro', fr: 'Macro', ar: 'الاقتصاد الكلي', de: 'Makro' },
    'nav.guardian': { en: 'Guardian', es: 'Guardián', zh: '守護者', pt: 'Guardião', fr: 'Gardien', ar: 'الحارس', de: 'Guardian' },
    'nav.signals': { en: 'Signals', es: 'Señales', zh: '信號', pt: 'Sinais', fr: 'Signaux', ar: 'الإشارات', de: 'Signale' },
    'nav.deepscan': { en: 'Deep Scan', es: 'Escaneo profundo', zh: '深度掃描', pt: 'Varredura profunda', fr: 'Analyse approfondie', ar: 'فحص عميق', de: 'Tiefenscan' },
    'nav.feed': { en: 'Live Feed', es: 'Feed en vivo', zh: '即時動態', pt: 'Feed ao vivo', fr: 'Flux en direct', ar: 'البث المباشر', de: 'Live-Feed' },
    'nav.trade': { en: 'Trade', es: 'Operar', zh: '交易', pt: 'Operar', fr: 'Trader', ar: 'تداول', de: 'Handel' },
    'nav.portfolio': { en: 'Portfolio', es: 'Cartera', zh: '投資組合', pt: 'Carteira', fr: 'Portefeuille', ar: 'المحفظة', de: 'Portfolio' },
    'nav.leaderboard': { en: 'Leaders', es: 'Líderes', zh: '排行榜', pt: 'Líderes', fr: 'Classement', ar: 'المتصدرون', de: 'Bestenliste' },
    'nav.lab': { en: 'Lab', es: 'Laboratorio', zh: '實驗室', pt: 'Laboratório', fr: 'Labo', ar: 'المختبر', de: 'Labor' },
    'nav.engine': { en: 'Engine', es: 'Motor', zh: '引擎', pt: 'Motor', fr: 'Moteur', ar: 'المحرك', de: 'Engine' },
    'nav.account': { en: 'Account', es: 'Cuenta', zh: '帳戶', pt: 'Conta', fr: 'Compte', ar: 'الحساب', de: 'Konto' },

    // Dashboard view headers (title + subtitle), emitted centrally by viewHead.
    'vh.home.title': { en: 'Home', es: 'Inicio', zh: '首頁', pt: 'Início', fr: 'Accueil', ar: 'الرئيسية', de: 'Start' },
    'vh.home.sub': { en: 'Your account at a glance', es: 'Tu cuenta de un vistazo', zh: '一覽你的帳戶', pt: 'Sua conta num relance', fr: 'Votre compte en un coup d’œil', ar: 'حسابك في لمحة', de: 'Dein Konto auf einen Blick' },
    'vh.markets.title': { en: 'Markets', es: 'Mercados', zh: '市場', pt: 'Mercados', fr: 'Marchés', ar: 'الأسواق', de: 'Märkte' },
    'vh.markets.sub': { en: 'Live exchange data', es: 'Datos del exchange en vivo', zh: '即時交易所數據', pt: 'Dados da exchange ao vivo', fr: 'Données d’échange en direct', ar: 'بيانات المنصّة المباشرة', de: 'Live-Börsendaten' },
    'vh.signals.title': { en: 'Signals', es: 'Señales', zh: '信號', pt: 'Sinais', fr: 'Signaux', ar: 'الإشارات', de: 'Signale' },
    'vh.signals.sub': { en: 'Every setup the engine generates — taken or not', es: 'Cada oportunidad que genera el motor, tomada o no', zh: '引擎產生的每個交易機會——無論是否採用', pt: 'Cada setup que o motor gera, executado ou não', fr: 'Chaque configuration générée par le moteur, prise ou non', ar: 'كل فرصة يولّدها المحرك، سواء نُفّذت أم لا', de: 'Jedes Setup der Engine — genommen oder nicht' },
    'vh.deepscan.title': { en: 'Deep Scan', es: 'Escaneo profundo', zh: '深度掃描', pt: 'Varredura profunda', fr: 'Analyse approfondie', ar: 'فحص عميق', de: 'Tiefenscan' },
    'vh.deepscan.sub': { en: 'The engine’s per-symbol pattern read — chart & candlestick', es: 'La lectura de patrones del motor por símbolo: gráfico y velas', zh: '引擎對每個標的的形態解讀——圖表與 K 線', pt: 'A leitura de padrões do motor por símbolo — gráfico e candles', fr: 'La lecture des motifs par symbole du moteur — graphique et chandeliers', ar: 'قراءة المحرك للأنماط لكل رمز — الرسم والشموع', de: 'Formationslesung je Symbol — Chart & Kerzen' },
    'vh.trade.title': { en: 'Trade', es: 'Operar', zh: '交易', pt: 'Operar', fr: 'Trader', ar: 'تداول', de: 'Handel' },
    'vh.trade.sub': { en: 'Manual trading through the engine’s risk gate', es: 'Operativa manual a través del filtro de riesgo del motor', zh: '透過引擎風險閘進行手動交易', pt: 'Operação manual pelo filtro de risco do motor', fr: 'Trading manuel via le filtre de risque du moteur', ar: 'تداول يدوي عبر بوابة مخاطر المحرك', de: 'Manueller Handel durch das Risikotor der Engine' },
    'vh.portfolio.title': { en: 'Portfolio', es: 'Cartera', zh: '投資組合', pt: 'Carteira', fr: 'Portefeuille', ar: 'المحفظة', de: 'Portfolio' },
    'vh.portfolio.sub': { en: 'Your equity, history, and journal', es: 'Tu capital, historial y diario', zh: '你的權益、歷史與交易日誌', pt: 'Seu patrimônio, histórico e diário', fr: 'Vos fonds, votre historique et votre journal', ar: 'رأس مالك وسجلّك ومذكّرتك', de: 'Dein Kapital, deine Historie und dein Journal' },
    'vh.engine.title': { en: 'Engine', es: 'Motor', zh: '引擎', pt: 'Motor', fr: 'Moteur', ar: 'المحرك', de: 'Engine' },
    'vh.engine.sub': { en: 'The autonomous RUNECLAW engine, live', es: 'El motor autónomo de RUNECLAW, en vivo', zh: '自主運行的 RUNECLAW 引擎，實時', pt: 'O motor autônomo da RUNECLAW, ao vivo', fr: 'Le moteur autonome RUNECLAW, en direct', ar: 'محرك RUNECLAW المستقل، مباشرةً', de: 'Die autonome RUNECLAW-Engine, live' },
    'vh.account.title': { en: 'Account', es: 'Cuenta', zh: '帳戶', pt: 'Conta', fr: 'Compte', ar: 'الحساب', de: 'Konto' },
    'vh.account.sub': { en: 'Profile, connections, and live-trading controls', es: 'Perfil, conexiones y controles de operativa real', zh: '個人資料、連接與實盤交易控制', pt: 'Perfil, conexões e controles de trading real', fr: 'Profil, connexions et contrôles de trading réel', ar: 'الملف الشخصي والاتصالات وضوابط التداول الحقيقي', de: 'Profil, Verbindungen und Live-Handelssteuerung' },
    'vh.leaderboard.title': { en: 'Leaderboard', es: 'Clasificación', zh: '排行榜', pt: 'Classificação', fr: 'Classement', ar: 'لوحة المتصدرين', de: 'Bestenliste' },
    'vh.leaderboard.sub': { en: 'Opt-in ranks by return % — anonymous handles, no dollar amounts', es: 'Rankings voluntarios por % de rendimiento: alias anónimos, sin importes', zh: '自願參與、按報酬率排名——匿名代號，不顯示金額', pt: 'Ranking opcional por % de retorno — apelidos anônimos, sem valores', fr: 'Classements volontaires par % de rendement — pseudos anonymes, sans montants', ar: 'ترتيب اختياري حسب نسبة العائد — أسماء مستعارة، دون مبالغ', de: 'Freiwillige Ränge nach Rendite % — anonyme Namen, keine Geldbeträge' },
    'vh.lab.title': { en: 'Strategy Lab', es: 'Laboratorio de estrategias', zh: '策略實驗室', pt: 'Laboratório de estratégias', fr: 'Labo de stratégies', ar: 'مختبر الاستراتيجيات', de: 'Strategielabor' },
    'vh.lab.sub': { en: 'Run the engine’s honest backtester on frozen benchmark data', es: 'Ejecuta el backtester honesto del motor sobre datos de referencia congelados', zh: '在凍結的基準數據上運行引擎的誠實回測器', pt: 'Rode o backtester honesto do motor em dados de referência congelados', fr: 'Lancez le backtester honnête du moteur sur des données de référence figées', ar: 'شغّل أداة الاختبار الخلفي الأمينة للمحرك على بيانات مرجعية مجمّدة', de: 'Führe den ehrlichen Backtester der Engine auf eingefrorenen Benchmark-Daten aus' },
    'vh.hub.title': { en: 'Agent Hub', es: 'Centro del agente', zh: '智能體中心', pt: 'Central do agente', fr: 'Hub de l’agent', ar: 'مركز الوكيل', de: 'Agenten-Hub' },
    'vh.hub.sub': { en: 'Everything your agent does — status at a glance, one tap to act', es: 'Todo lo que hace tu agente: estado de un vistazo, una pulsación para actuar', zh: '你的智能體所做的一切——狀態一目了然，一鍵操作', pt: 'Tudo o que seu agente faz — status num relance, um toque para agir', fr: 'Tout ce que fait votre agent — statut en un coup d’œil, une touche pour agir', ar: 'كل ما يفعله وكيلك — الحالة في لمحة، نقرة واحدة للتنفيذ', de: 'Alles, was dein Agent tut — Status auf einen Blick, Aktion mit einem Tipp' },
    'vh.feed.title': { en: 'Live Feed', es: 'Feed en vivo', zh: '即時動態', pt: 'Feed ao vivo', fr: 'Flux en direct', ar: 'البث المباشر', de: 'Live-Feed' },

    // Home first-run welcome (rendered synchronously, so apply() catches it).
    'home.welcome_title': { en: 'Meet your agent', es: 'Conoce a tu agente', zh: '認識你的智能體', pt: 'Conheça seu agente', fr: 'Rencontrez votre agent', ar: 'تعرّف على وكيلك', de: 'Lerne deinen Agenten kennen' },
    'home.welcome_body': { en: 'Welcome to RUNECLAW. From here on, an autonomous trading agent works this dashboard with you — it scans the market around the clock, explains every read, and only ever trades through a strict risk gate. Three good first moves:', es: 'Bienvenido a RUNECLAW. A partir de ahora, un agente de trading autónomo trabaja en este panel contigo: analiza el mercado sin descanso, explica cada lectura y solo opera a través de un estricto filtro de riesgo. Tres buenos primeros pasos:', zh: '歡迎使用 RUNECLAW。從現在起，一個自主交易智能體將與你一起使用此儀表板——它全天候掃描市場、解釋每一次判讀，並且只透過嚴格的風險閘進行交易。三個不錯的起手式：', pt: 'Bem-vindo à RUNECLAW. Daqui em diante, um agente de trading autônomo trabalha neste painel com você — varre o mercado o tempo todo, explica cada leitura e só opera por um filtro de risco rígido. Três bons primeiros passos:', fr: 'Bienvenue sur RUNECLAW. Désormais, un agent de trading autonome utilise ce tableau de bord avec vous — il analyse le marché en continu, explique chaque lecture et ne trade qu’à travers un filtre de risque strict. Trois bons premiers gestes :', ar: 'مرحبًا بك في RUNECLAW. من الآن فصاعدًا، يعمل وكيل تداول مستقل على هذه اللوحة معك — يفحص السوق على مدار الساعة، ويشرح كل قراءة، ولا يتداول إلا عبر بوابة مخاطر صارمة. ثلاث خطوات أولى جيدة:', de: 'Willkommen bei RUNECLAW. Ab jetzt arbeitet ein autonomer Handelsagent gemeinsam mit dir an diesem Dashboard — er scannt den Markt rund um die Uhr, erklärt jede Einschätzung und handelt ausschließlich durch ein strenges Risikotor. Drei gute erste Schritte:' },
    'home.welcome_1': { en: '💬 1 · Say hello to your agent', es: '💬 1 · Saluda a tu agente', zh: '💬 1 · 向你的智能體打招呼', pt: '💬 1 · Diga olá ao seu agente', fr: '💬 1 · Dites bonjour à votre agent', ar: '💬 1 · ألقِ التحية على وكيلك', de: '💬 1 · Begrüße deinen Agenten' },
    'home.welcome_2': { en: '📡 2 · Watch it read the market', es: '📡 2 · Observa cómo lee el mercado', zh: '📡 2 · 觀看它解讀市場', pt: '📡 2 · Veja-o ler o mercado', fr: '📡 2 · Regardez-le lire le marché', ar: '📡 2 · شاهده يقرأ السوق', de: '📡 2 · Sieh zu, wie er den Markt liest' },
    'home.welcome_3': { en: '🎯 3 · Place a risk-gated paper trade', es: '🎯 3 · Coloca una operación en papel con filtro de riesgo', zh: '🎯 3 · 下一筆經風險閘的模擬交易', pt: '🎯 3 · Faça uma operação em papel com filtro de risco', fr: '🎯 3 · Passez un trade papier filtré par le risque', ar: '🎯 3 · نفّذ صفقة تجريبية عبر بوابة المخاطر', de: '🎯 3 · Platziere einen risikogeprüften Spielgeld-Trade' },
    'home.welcome_dismiss': { en: 'Got it — don’t show again', es: 'Entendido, no mostrar de nuevo', zh: '知道了——不再顯示', pt: 'Entendi — não mostrar de novo', fr: 'Compris — ne plus afficher', ar: 'حسنًا — لا تُظهرها مرّة أخرى', de: 'Verstanden — nicht mehr anzeigen' },
    'vh.feed.sub': { en: 'The agent’s mind-stream — every scan, thesis, trade and alert, as it happens', es: 'El flujo mental del agente: cada análisis, tesis, operación y alerta, en tiempo real', zh: '智能體的思緒流——每次掃描、論點、交易與警報，實時呈現', pt: 'O fluxo de pensamento do agente — cada varredura, tese, operação e alerta, em tempo real', fr: 'Le flux de pensée de l’agent — chaque analyse, thèse, trade et alerte, en direct', ar: 'تدفّق تفكير الوكيل — كل مسح وأطروحة وصفقة وتنبيه، لحظيًا', de: 'Der Gedankenstrom des Agenten — jeder Scan, jede These, jeder Trade und Alarm, in Echtzeit' },
  };

  function normalize(code) {
    if (!code) return '';
    return String(code).trim().toLowerCase().replace(/_/g, '-').split('-')[0];
  }
  function codes() { return LANGS.map(function (l) { return l.code; }); }
  function supported(code) { return codes().indexOf(normalize(code)) >= 0; }

  function resolveLang(stored, nav) {
    if (supported(stored)) return normalize(stored);
    if (supported(nav)) return normalize(nav);
    return 'en';
  }

  function translate(key, lang) {
    var e = STRINGS[key];
    if (!e) return null;
    if (e[lang] != null) return e[lang];
    return e.en != null ? e.en : null;
  }

  // ── Browser-only from here ────────────────────────────────────────────────
  var current = 'en';

  function setAttrs(el, lang) {
    // data-i18n-attr="placeholder:key;aria-label:key2"
    var spec = el.getAttribute('data-i18n-attr');
    spec.split(';').forEach(function (pair) {
      var i = pair.indexOf(':');
      if (i < 0) return;
      var attr = pair.slice(0, i).trim();
      var v = translate(pair.slice(i + 1).trim(), lang);
      if (v != null) el.setAttribute(attr, v);
    });
  }

  function apply(scope, lang) {
    if (lang == null) lang = current;      // default to the active language
    var doc = scope || document;
    doc.querySelectorAll('[data-i18n]').forEach(function (el) {
      var v = translate(el.getAttribute('data-i18n'), lang);
      if (v != null) el.textContent = v;
    });
    doc.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      var v = translate(el.getAttribute('data-i18n-html'), lang);
      if (v != null) el.innerHTML = v;
    });
    doc.querySelectorAll('[data-i18n-attr]').forEach(function (el) {
      setAttrs(el, lang);
    });
  }

  function persistServer(lang) {
    // Best-effort: store the logged-in user's prefs.lang so AI chat localizes.
    try {
      var token = localStorage.getItem('token');
      if (!token) return;
      fetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
        body: JSON.stringify({ prefs: { lang: lang } }),
      }).catch(function () {});
    } catch (e) { /* ignore */ }
  }

  function setLang(lang, opts) {
    opts = opts || {};
    current = supported(lang) ? normalize(lang) : 'en';
    try { localStorage.setItem('rc_lang', current); } catch (e) { /* ignore */ }
    var el = document.documentElement;
    if (el) {
      el.setAttribute('lang', current);
      el.setAttribute('dir', RTL[current] ? 'rtl' : 'ltr');
    }
    apply(document, current);
    var sel = document.getElementById('rc-lang-select');
    if (sel && sel.value !== current) sel.value = current;
    if (opts.persistServer !== false) persistServer(current);
    try { root.dispatchEvent(new CustomEvent('rc-lang', { detail: current })); } catch (e) { /* ignore */ }
  }

  function buildSwitcher() {
    if (document.getElementById('rc-lang-select')) return;
    var host = document.querySelector('[data-i18n-switcher]')
      || document.querySelector('nav.topbar .nav-links');
    if (!host) return;
    var sel = document.createElement('select');
    sel.id = 'rc-lang-select';
    sel.className = 'rc-lang-select';
    sel.setAttribute('aria-label', translate('lang.label', current) || 'Language');
    LANGS.forEach(function (l) {
      var o = document.createElement('option');
      o.value = l.code; o.textContent = l.name;
      if (l.code === current) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener('change', function () { setLang(sel.value); });
    // Place before the primary CTA when we can, so it reads as a nav control.
    var cta = host.querySelector('.btn--primary');
    if (cta) host.insertBefore(sel, cta); else host.appendChild(sel);
  }

  function init() {
    var stored = null;
    try { stored = localStorage.getItem('rc_lang'); } catch (e) { /* ignore */ }
    var nav = (typeof navigator !== 'undefined') ? navigator.language : '';
    current = resolveLang(stored, nav);
    var el = document.documentElement;
    if (el) {
      el.setAttribute('lang', current);
      el.setAttribute('dir', RTL[current] ? 'rtl' : 'ltr');
    }
    buildSwitcher();
    if (current !== 'en') apply(document, current);
  }

  var api = {
    LANGS: LANGS, STRINGS: STRINGS,
    normalize: normalize, supported: supported, resolveLang: resolveLang,
    translate: translate, apply: apply, setLang: setLang,
    getLang: function () { return current; }, init: init,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document !== 'undefined') {
    root.RCI18N = api;
    if (document.readyState !== 'loading') init();
    else document.addEventListener('DOMContentLoaded', init);
  }
})(typeof window !== 'undefined' ? window : globalThis);
