/* The selected type and layout belong to the project, just like its filters. */
function stopRomAssetBrowser() {
  stopRomTextureGallery();
  stopRomModelBrowser();
  stopRomAudioBrowser();
}

function renderRomAssetBrowser(view) {
  const browser=$('romAssetBrowser');
  browser.hidden=!view.report?.available;
  if(browser.hidden)return;
  const types=[
    ['Texture',renderRomTextureGallery],
    ['Model',renderRomModelBrowser],
    ['Audio',renderRomAudioBrowser],
  ];
  const state=view.assetBrowser||={type:0,layout:'icons'};

  function draw() {
    stopRomAssetBrowser();
    types.forEach(([type],index)=>{
      const selected=index===state.type,tab=$('rom'+type+'Tab');
      tab.setAttribute('aria-selected',String(selected));
      tab.classList.toggle('on',selected);
      tab.tabIndex=selected?0:-1;
      $('rom'+type+'Panel').hidden=!selected;
    });
    for(const [id,layout] of [['romAssetIcons','icons'],['romAssetTable','table']]){
      const button=$(id),selected=state.layout===layout;
      button.setAttribute('aria-pressed',String(selected));
      button.classList.toggle('on',selected);
    }
    if(browser.open)types[state.type][1](view);
  }

  types.forEach(([type],index)=>{
    const tab=$('rom'+type+'Tab');
    tab.onclick=()=>{if(state.type!==index){state.type=index;draw();}};
    tab.onkeydown=event=>{
      const destinations={ArrowLeft:(index+types.length-1)%types.length,
        ArrowRight:(index+1)%types.length,Home:0,End:types.length-1};
      const next=destinations[event.key];
      if(next===undefined)return;
      event.preventDefault();
      state.type=next;
      draw();
      $('rom'+types[next][0]+'Tab').focus();
    };
  });
  for(const [id,layout] of [['romAssetIcons','icons'],['romAssetTable','table']]){
    $(id).onclick=()=>{if(state.layout!==layout){state.layout=layout;draw();}};
  }
  browser.ontoggle=()=>{if(!browser.hidden && S.tab==='rom')draw();};
  draw();
}

function sortRomAssetRows(rows,state) {
  rows.sort((a,b)=>{
    const x=a[state.sort],y=b[state.sort];
    if(x==null||y==null)return x==null?(y==null?0:1):-1;
    return state.direction*(typeof x==='number'?x-y:String(x).localeCompare(String(y),undefined,{numeric:true}))
      || a.key.localeCompare(b.key);
  });
}

function renderRomAssetTable(host,columns,state,rows,draw) {
  const headings=columns.map(([key,label])=>{
    if(!key)return `<th scope="col">${label}</th>`;
    const sort=state.sort!==key?'none':state.direction===1?'ascending':'descending';
    const arrow=sort==='none'?'↕':sort==='ascending'?'↑':'↓';
    return `<th scope="col" aria-sort="${sort}"><button type="button" class="table-sort" data-asset-sort="${key}">${label} ${arrow}</button></th>`;
  }).join('');
  host.innerHTML=`<div class="compiler-table"><table><thead><tr>${headings}</tr></thead><tbody>${rows.map(cells=>`<tr><td>${cells.join('</td><td>')}</td></tr>`).join('')}</tbody></table></div>`;
  host.querySelectorAll('[data-asset-sort]').forEach(button=>button.onclick=()=>{
    const key=button.dataset.assetSort;
    state.direction=state.sort===key?-state.direction:1;
    state.sort=key;
    draw();
  });
}

function renderRomAssetCards(host,icon,columns,rows) {
  host.innerHTML=`<div class="rom-asset-grid">${rows.map(([title,...cells])=>`<article class="rom-texture-card">
    <span class="rom-asset-icon" aria-hidden="true">${icon}</span>${title}
    <dl>${cells.slice(0,-1).map((cell,i)=>`<dt>${columns[i+1][1]}</dt><dd>${cell}</dd>`).join('')}</dl>
    ${cells.at(-1)}</article>`).join('')}</div>`;
}
