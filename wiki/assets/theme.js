(function(){
  var KEY='hsr-theme';
  var ORDER=['system','light','dark'];
  function read(){ try{ var v=localStorage.getItem(KEY); return (v==='light'||v==='dark'||v==='system')?v:'system'; }catch(e){ return 'system'; } }
  function save(v){ try{ localStorage.setItem(KEY,v); }catch(e){} }
  function apply(v){
    var el=document.documentElement;
    if(v==='system'){ el.removeAttribute('data-theme'); } else { el.setAttribute('data-theme',v); }
  }
  function label(v){ return v==='system' ? '跟随系统' : (v==='dark' ? '暗色' : '亮色'); }
  var state=read(); apply(state);
  function paint(){
    var b=document.getElementById('theme-toggle'); if(!b) return;
    b.textContent=label(state);
    b.setAttribute('aria-label','当前主题：'+label(state)+'（点击切换）');
    b.setAttribute('title','当前主题：'+label(state)+'（依次切换：跟随系统 → 亮色 → 暗色）');
  }
  function toggle(){
    state=ORDER[(ORDER.indexOf(state)+1)%ORDER.length];
    apply(state); save(state); paint();
  }
  document.addEventListener('DOMContentLoaded',function(){
    var b=document.getElementById('theme-toggle');
    if(b) b.addEventListener('click',toggle);
    paint();
    // #14-1：--ui-topbar-h 以**实测**为准。CSS 里的 calc 只作首帧 / 无 JS 兜底，
    // 这里量 .topbar 的 offsetHeight 并内联覆盖 :root 变量（改字号、改 padding 都不会失配）。
    try{
      var root=document.documentElement;
      var bar=document.querySelector('.topbar');
      var syncTopbar=function(){
        var h=bar?bar.offsetHeight:0;
        if(h>0){ root.style.setProperty('--ui-topbar-h',h+'px'); }
      };
      syncTopbar();
      window.addEventListener('resize',syncTopbar,{passive:true});
      if(window.ResizeObserver && bar){ new ResizeObserver(syncTopbar).observe(bar); }
    }catch(e){ /* 老浏览器：保留 CSS 兜底值 */ }
    // 小屏（≤640px）：默认收起分类导航，避免首屏被导航占满；桌面（>640px）保持展开
    try{
      if(window.matchMedia && window.matchMedia('(max-width:640px)').matches){
        document.querySelectorAll('.sidenav details[open]').forEach(function(d){ d.removeAttribute('open'); });
      }
    }catch(e){ /* 老浏览器忽略 */ }
  });
})();
