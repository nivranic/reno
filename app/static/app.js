/* Four-linkage detail page: player <-> timeline <-> evidence stream <-> atoms.
   Click atom -> seek + real-time frame fetch (frame-accurate, ±0s). */
const vid = document.getElementById('detail').dataset.vid;
const player = document.getElementById('player');
const tl = document.getElementById('timeline');
const evBox = document.getElementById('events');
const atomBox = document.getElementById('atoms');
const frameBox = document.getElementById('frame-box');
const frameImg = document.getElementById('frame-img');
const frameTs = document.getElementById('frame-ts');

function ts(ms){const s=ms/1000;return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(Math.floor(s%60)).padStart(2,'0')}.${Math.floor((s%1)*10)}`}

async function showFrame(ms){
  frameBox.style.display='block';
  frameTs.textContent='帧 @ '+ts(ms);
  frameImg.src=`/api/frame/${vid}/${ms}?r=${Date.now()}`;
}

function seek(ms){ player.currentTime = ms/1000; player.play(); showFrame(ms); }

fetch(`/api/video/${vid}/events`).then(r=>r.json()).then(({events,atoms})=>{
  const dur = player.duration || parseFloat(player.dataset.dur||0);
  function layout(){
    const D = player.duration || 1;
    tl.innerHTML='';
    for(const e of events){
      const d=document.createElement('div');
      d.className=`tick ${e.mod.toLowerCase()}`;
      d.style.left=(e.ms/D*100)+'%';
      d.title=`[${e.mod}] ${ts(e.ms)} ${e.text}`;
      d.onclick=()=>seek(e.ms);
      tl.appendChild(d);
    }
  }
  player.addEventListener('loadedmetadata',layout);
  setTimeout(layout,800);

  for(const e of events){
    const d=document.createElement('div');
    d.className='ev';
    d.innerHTML=`<span class="t">${ts(e.ms)}</span><b class="tag ${e.mod.toLowerCase()}">${e.mod}</b> ${e.text}`;
    d.onclick=()=>seek(e.ms);
    evBox.appendChild(d);
  }

  // highlight active events while playing
  player.addEventListener('timeupdate',()=>{
    const ms=player.currentTime*1000;
    [...evBox.children].forEach((el,i)=>{
      const e=events[i];
      el.style.background=(ms>=e.ms&&ms<=e.end)?'#e8f0fe':'';
    });
  });

  for(const a of atoms){
    const card=document.createElement('div');
    card.className='atom-card';
    const params=(a.parameters||[]).map(p=>`${p.name}=${p.value}${p.unit||''}`).join(' · ');
    card.innerHTML=`<div class="claim"><span class="badge p-${a.polarity}">${a.polarity}</span> ${a.claim}</div>
      <div class="meta">${a.category}·${a.space} · conf=${a.confidence} · ${a.status}${params?' · '+params:''}</div>
      <div class="evs">${a.evidence.map(e=>`<span class="ev-link" data-ms="${e.ms}">▸ ${e.mod} ${ts(e.ms)} ${(e.text||'').slice(0,18)}</span>`).join('')}</div>`;
    card.onclick=()=>seek(a.evidence[0].ms);
    card.querySelectorAll('.ev-link').forEach(l=>l.onclick=(ev)=>{ev.stopPropagation();seek(+l.dataset.ms)});
    atomBox.appendChild(card);
  }
});

// deep-link ?t=ms (from search results)
const t=new URLSearchParams(location.search).get('t');
if(t){ player.addEventListener('loadedmetadata',()=>{seek(+t)}); }
