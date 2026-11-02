// Reads a LinkedIn profile page into plain fields. LinkedIn's markup changes often (newer layouts
// have no <main> and no stable section ids), so this reads the page's visible text and splits it
// at the section headings LinkedIn shows ("About", "Experience", ...). The DOM is only used for
// the photo and, when present, the <h1> name.
const SECTION_HEADINGS = [
  "About", "Featured", "Activity", "Experience", "Education", "Licenses & certifications",
  "Projects", "Skills", "Recommendations", "Courses", "Honors & awards", "Languages",
  "Interests", "Volunteering", "Publications", "Organizations", "Test scores", "Patents",
  "Causes", "Services", "Connected apps",
];
// The profile column ends where LinkedIn's right-hand sidebar starts.
const SIDEBAR_START = /^(Profile language|Public profile & URL|Who your viewers also viewed|People also viewed|People you may know|More profiles for you|You might like)$/;
// Buttons and prompts that aren't profile content.
const UI_NOISE = /^(Show all.*|Show (more|less|details)|…\s*see more|see more|Create a post|Add (section|services|profile section)|Edit.*|Private to you|Get started|Resources|Contact info|Endorse|Message|Connect|Follow|·)$/i;

function profileLines(doc) {
  const root = doc.querySelector("main") || doc.body;
  // innerText skips display:none; hide LinkedIn's screen-reader duplicates while reading.
  const style = doc.createElement("style");
  style.textContent = ".vzb-raw .visually-hidden { display: none !important; }";
  doc.head.appendChild(style);
  root.classList.add("vzb-raw");
  const text = root.innerText || "";
  root.classList.remove("vzb-raw");
  style.remove();

  const lines = text.split("\n").map(l => l.replace(/\s+/g, " ").trim()).filter(Boolean);
  const end = lines.findIndex(l => SIDEBAR_START.test(l));
  return end === -1 ? lines : lines.slice(0, end);
}

// Some headings carry a count, like "Skills (42)"; that count is kept in `counts`.
function splitSections(lines) {
  const sections = { _top: [] }, counts = {};
  let current = "_top";
  for (const line of lines) {
    const m = line.match(/^(.+?)\s*\((\d[\d,]*)\)$/);
    const heading = m ? m[1] : line;
    if (SECTION_HEADINGS.includes(heading) && !(heading in sections)) {
      current = heading; sections[heading] = [];
      if (m) counts[heading] = +m[2].replace(/,/g, "");
      continue;
    }
    sections[current].push(line);
  }
  return { sections, counts };
}

// LinkedIn serves profile photos from URLs containing "profile-displayphoto" (banners are
// "profile-displaybackgroundimage"; a missing photo is a static placeholder). The top-card photo
// is large, unlike the small avatars in the nav bar and sidebar, so look for a large element
// showing a profile-photo URL, as an <img> (src or srcset) or a CSS background.
function findProfilePhoto(doc, name) {
  const imageUrl = el => el.tagName === "IMG"
    ? [el.currentSrc, el.src, el.srcset, el.dataset?.delayedUrl].filter(Boolean).join(" ")
    : getComputedStyle(el).backgroundImage;
  const kind = url => (url.match(/profile-display(photo|backgroundimage)|company-logo|ghost|static\.licdn\.com/) || ["other"])[0];
  const size = el => { const r = el.getBoundingClientRect(); return Math.round(Math.max(r.width, el.width || 0)); };

  // Backgrounds can come from stylesheets, so check computed styles (only for elements big enough).
  const backgrounds = [...doc.querySelectorAll("div, span, a, button, figure")]
    .filter(el => el.offsetWidth >= 80 && getComputedStyle(el).backgroundImage.includes("media.licdn.com"));
  const candidates = [...doc.images, ...backgrounds]
    .map(el => ({ el, url: imageUrl(el), px: size(el) }))
    .filter(c => c.px >= 80);
  const isPhoto = c => /profile-displayphoto/.test(c.url)
    || (c.el.tagName === "IMG" && name && (c.el.alt || "").includes(name) && /media\.licdn\.com/.test(c.url));

  // A short description of each large image, so a miss on a new layout can be diagnosed.
  const imageHints = candidates.slice(0, 8).map(c =>
    `${c.px}px ${c.el.tagName.toLowerCase()} ${kind(c.url)} alt="${(c.el.alt || "").slice(0, 40)}"`);
  // The largest matching photo's address, so the app can compare it with your other profiles.
  const photo = candidates.filter(isPhoto).sort((a, b) => b.px - a.px)[0];
  const photoUrl = photo ? (photo.url.match(/https:\/\/[^\s"')]+profile-displayphoto[^\s"')]*/) || [""])[0] : "";
  return { hasPhoto: !!photo, photoUrl, imageHints };
}

function extractLinkedInProfile(doc = document, href = location.href) {
  const lines = profileLines(doc);
  const { sections: s, counts } = splitSections(lines);
  const content = name => (s[name] || []).filter(l => !UI_NOISE.test(l));
  const top = s._top;

  // Name: the <h1> if this layout has one, else the tab title ("(3) Jane Doe | LinkedIn").
  const titleName = doc.title.replace(/^\(\d+\)\s*/, "").split("|")[0].trim();
  const name = (doc.querySelector("h1")?.textContent || "").trim() || titleName || top[0] || "";

  // Headline: the first real line under the name in the top card (skipping badges like
  // "Verify in 2 minutes", pronouns and the "·" separator).
  const afterName = top.slice(Math.max(0, top.indexOf(name)) + 1)
    .filter(l => !UI_NOISE.test(l) && !/verify|^\(?(she|he|they)\s*\/|^\d[\d,]* (connections|followers)$/i.test(l));
  const headline = afterName[0] || "";

  // About holds a "Top skills" line on newer layouts; keep it out of the About text.
  const about = content("About");
  const topSkillsAt = about.findIndex(l => /^Top skills$/i.test(l));
  const topSkills = topSkillsAt === -1 ? [] : (about[topSkillsAt + 1] || "").split(/\s*•\s*/).filter(Boolean);
  const aboutText = (topSkillsAt === -1 ? about : about.slice(0, topSkillsAt)).join("\n");

  // Under each skill LinkedIn adds where you used it ("Backend Engineer at AnswerIQ") and
  // endorsement lines; keep only the skill names.
  const skillsSection = content("Skills").filter(l =>
    !/endorsement|^Endorsed by|experiences? (at|across)|\s(at|@)\s|\d+ (skills?|more)$/i.test(l));
  const skills = [...new Set([...topSkills, ...skillsSection])];

  // Activity shows each post's age as "3d", "2w", "1mo", "1yr"; the smallest is the latest post.
  const DAYS = { m: 0, h: 0, d: 1, w: 7, mo: 30, yr: 365 };
  const activityText = (s.Activity || []).join("\n");
  const ages = [...activityText.matchAll(/\b(\d+)\s?(mo|yr|m|h|d|w)(?![a-z])/g)].map(([, n, u]) => +n * DAYS[u]);

  // "Received (N)" when LinkedIn shows it. With no Recommendations section, it's 0 only if the
  // page clearly finished loading (Experience is present); otherwise unknown.
  const recLines = (s.Recommendations || []).join("\n");
  const received = recLines.match(/Received\s*\((\d+)\)/i);
  const recommendationsReceived = received ? +received[1]
    : /haven.t received/i.test(recLines) ? 0
    : s.Recommendations ? null : (s.Experience ? 0 : null);

  const { hasPhoto, photoUrl, imageHints } = findProfilePhoto(doc, name);

  const slug = (href.match(/linkedin\.com\/in\/([^/?#]+)/) || [])[1] || "";

  return {
    url: slug ? `https://www.linkedin.com/in/${slug}/` : href,
    name,
    headline,
    about: aboutText,
    experience: content("Experience").join("\n"),
    skills: skills.join("\n"),
    // Total skills on the profile ("Skills (42)"); the page only shows a few of them.
    skillsCount: counts.Skills ?? (skills.length || null),
    education: content("Education").join("\n"),
    hasPhoto,
    imageHints,
    photoUrl,
    hasFeatured: !!s.Featured,
    recommendationsReceived,
    hasActivitySection: !!s.Activity,
    lastPostDays: ages.length ? Math.min(...ages) : null,
    // Edit controls only appear on the viewer's own profile.
    looksLikeOwnProfile: /(^|\n|\b)(Add (profile )?section|Open to|Enhance profile)/i.test(top.join("\n")),
    // Which sections were on the page, so a missing one can be told apart from an empty one.
    sectionsFound: Object.keys(s).filter(k => k !== "_top"),
    raw: lines.join("\n").slice(0, 30000),
  };
}
